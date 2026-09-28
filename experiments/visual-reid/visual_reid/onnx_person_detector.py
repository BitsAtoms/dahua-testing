"""Offline ONNX person detection. Runtime selection and YOLOX decoding are separate."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import cv2
import numpy as np

from .detector_benchmark import Detection, non_max_suppression, _yolox_grids


class ProviderError(RuntimeError):
    pass


def select_provider(ort, options, provider: str, device_index: int | None):
    if provider == "cpu":
        if device_index is not None:
            raise ProviderError("CPU does not accept a device index")
        return ["CPUExecutionProvider"]
    if provider != "directml" or device_index is None or device_index < 0:
        raise ProviderError("DirectML requires an explicit nonnegative provider-local device index")
    if not hasattr(ort, "get_ep_devices") or not hasattr(options, "add_provider_for_devices"):
        raise ProviderError("ORT lacks explicit EP device selection")
    devices = [item for item in ort.get_ep_devices() if str(item.ep_name) == "DmlExecutionProvider"]
    if device_index >= len(devices):
        raise ProviderError(f"DirectML device {device_index} unavailable; found {len(devices)}")
    options.add_provider_for_devices([devices[device_index]], {})
    return None


def verify_profile(events: list[dict], requested: str) -> dict:
    providers = {str(event.get("args", {}).get("provider")) for event in events
                 if event.get("args", {}).get("provider")}
    if requested not in providers:
        raise ProviderError(f"requested provider did not execute nodes: {sorted(providers)}")
    if requested != "CPUExecutionProvider" and "CPUExecutionProvider" in providers:
        raise ProviderError("CPU fallback found in ONNX Runtime node profile")
    return {"node_providers": sorted(providers), "cpu_fallback": False}


class OnnxRuntime:
    def __init__(self, path: Path, *, provider: str = "cpu", device_index: int | None = None):
        import onnxruntime as ort

        self.ort = ort
        self.requested = "CPUExecutionProvider" if provider == "cpu" else "DmlExecutionProvider"
        options = ort.SessionOptions()
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(tempfile.gettempdir()) / "visual-reid-detector")
        providers = select_provider(ort, options, provider, device_index)
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
        if self.requested not in self.session.get_providers():
            raise ProviderError(f"session lacks {self.requested}: {self.session.get_providers()}")
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1 or inputs[0].type != "tensor(float)":
            raise ValueError("expected one float input and one output")
        self.input_name = inputs[0].name
        self.input_shape = inputs[0].shape
        self.output_shape = outputs[0].shape

    def run(self, tensor: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: tensor})[0]

    def close(self) -> dict:
        profile_path = Path(self.session.end_profiling())
        try:
            return verify_profile(json.loads(profile_path.read_text(encoding="utf-8")), self.requested)
        finally:
            profile_path.unlink(missing_ok=True)


def preprocess_yolox(frame: np.ndarray, height: int, width: int) -> tuple[np.ndarray, float]:
    source_h, source_w = frame.shape[:2]
    if source_h <= 0 or source_w <= 0:
        raise ValueError("empty frame")
    ratio = min(height / source_h, width / source_w)
    resized = cv2.resize(frame, (int(source_w * ratio), int(source_h * ratio)))
    padded = np.full((height, width, 3), 114, dtype=np.uint8)
    padded[:resized.shape[0], :resized.shape[1]] = resized
    return np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32), ratio


def decode_yolox(output: np.ndarray, source_shape: tuple[int, int], ratio: float,
                 threshold: float, input_shape: tuple[int, int]) -> list[Detection]:
    height, width = input_shape
    grids, strides = _yolox_grids(height, width)
    if output.shape != (1, len(grids), 85):
        raise ValueError(f"unexpected YOLOX output shape: {output.shape}")
    rows = output[0]
    scores = rows[:, 4] * rows[:, 5]  # COCO person class 0
    selected = rows[scores >= threshold]
    selected_grids = grids[scores >= threshold]
    selected_strides = strides[scores >= threshold]
    selected_scores = scores[scores >= threshold]
    boxes = []
    source_h, source_w = source_shape
    for row, grid, stride, score in zip(selected, selected_grids, selected_strides, selected_scores):
        cx, cy = (row[:2] + grid) * stride
        box_w, box_h = np.exp(row[2:4]) * stride
        x1, y1 = max(0., (cx - box_w / 2) / ratio), max(0., (cy - box_h / 2) / ratio)
        x2, y2 = min(float(source_w), (cx + box_w / 2) / ratio), min(float(source_h), (cy + box_h / 2) / ratio)
        if x2 > x1 and y2 > y1:
            boxes.append(Detection((x1, y1, x2, y2), float(score)))
    return non_max_suppression(boxes)


class YoloXPersonDetector:
    def __init__(self, runtime: OnnxRuntime):
        self.runtime = runtime
        shape = runtime.input_shape
        if len(shape) != 4 or shape[:2] != [1, 3] or not all(isinstance(x, int) for x in shape[2:]):
            raise ValueError(f"unsupported YOLOX input shape: {shape}")
        self.height, self.width = shape[2:]
        expected = sum((self.height // stride) * (self.width // stride) for stride in (8, 16, 32))
        if runtime.output_shape != [1, expected, 85]:
            raise ValueError(f"unsupported YOLOX output shape: {runtime.output_shape}")

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        tensor, ratio = preprocess_yolox(frame, self.height, self.width)
        return decode_yolox(self.runtime.run(tensor), frame.shape[:2], ratio,
                            threshold, (self.height, self.width))
