"""Offline person-detector benchmark helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    score: float


def non_max_suppression(
    detections: list[Detection], iou_threshold: float = 0.45
) -> list[Detection]:
    pending = sorted(detections, key=lambda item: item.score, reverse=True)
    selected: list[Detection] = []
    while pending:
        best = pending.pop(0)
        selected.append(best)
        pending = [item for item in pending if _iou(best.box, item.box) < iou_threshold]
    return selected


def detection_runs(presence: list[bool], tolerated_gap: int = 1) -> int:
    """Count temporal detection fragments while bridging very short gaps."""
    runs = 0
    active = False
    gap = 0
    for present in presence:
        if present:
            if not active:
                runs += 1
                active = True
            gap = 0
        elif active:
            gap += 1
            if gap > tolerated_gap:
                active = False
                gap = 0
    return runs


def consensus_detections(
    first: list[Detection],
    second: list[Detection],
    *,
    iou_threshold: float = 0.3,
) -> list[Detection]:
    """Keep detections supported by a spatially overlapping second detector."""
    available = list(second)
    result: list[Detection] = []
    for candidate in sorted(first, key=lambda item: item.score, reverse=True):
        matches = [
            (index, _iou(candidate.box, other.box), other)
            for index, other in enumerate(available)
            if _iou(candidate.box, other.box) >= iou_threshold
        ]
        if not matches:
            continue
        index, _, other = max(matches, key=lambda item: item[1])
        available.pop(index)
        result.append(
            Detection(
                tuple((left + right) / 2 for left, right in zip(candidate.box, other.box)),
                min(candidate.score, other.score),
            )
        )
    return result


class ConsensusDetector:
    """Require agreement between a fixed-threshold guard and a candidate detector."""

    def __init__(
        self,
        guard: Any,
        candidate: Any,
        *,
        guard_threshold: float = 0.5,
        iou_threshold: float = 0.3,
    ) -> None:
        self.guard = guard
        self.candidate = candidate
        self.guard_threshold = guard_threshold
        self.iou_threshold = iou_threshold

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        return consensus_detections(
            self.guard.detect(frame, self.guard_threshold),
            self.candidate.detect(frame, threshold),
            iou_threshold=self.iou_threshold,
        )


class OpenVinoSsdDetector:
    def __init__(self, path: Path, device: str = "CPU") -> None:
        from openvino import Core

        core = Core()
        model = core.read_model(path)
        self.compiled = core.compile_model(model, device)
        self.input = self.compiled.input(0)
        shape = list(self.input.shape)
        self.height, self.width = int(shape[2]), int(shape[3])

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        height, width = frame.shape[:2]
        resized = cv2.resize(frame, (self.width, self.height))
        tensor = resized.transpose(2, 0, 1)[None].astype(np.float32)
        output = next(iter(self.compiled([tensor]).values())).reshape(-1, 7)
        result = []
        for _, label, score, x1, y1, x2, y2 in output:
            if int(label) != 1 or float(score) < threshold:
                continue
            result.append(
                Detection(
                    (float(x1 * width), float(y1 * height), float(x2 * width), float(y2 * height)),
                    float(score),
                )
            )
        return result


class OpenVinoYoloXDetector:
    def __init__(self, path: Path, device: str = "CPU") -> None:
        from openvino import Core

        core = Core()
        model = core.read_model(path)
        self.compiled = core.compile_model(model, device)
        shape = list(self.compiled.input(0).shape)
        self.height, self.width = int(shape[2]), int(shape[3])
        self.grids, self.strides = _yolox_grids(self.height, self.width)

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        original_h, original_w = frame.shape[:2]
        ratio = min(self.height / original_h, self.width / original_w)
        resized = cv2.resize(frame, (int(original_w * ratio), int(original_h * ratio)))
        padded = np.full((self.height, self.width, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        tensor = padded.transpose(2, 0, 1)[None].astype(np.float32)
        output = next(iter(self.compiled([tensor]).values()))[0].copy()
        output[:, :2] = (output[:, :2] + self.grids) * self.strides
        output[:, 2:4] = np.exp(output[:, 2:4]) * self.strides
        scores = output[:, 4] * output[:, 5]  # COCO class 0: person
        result = []
        for row, score in zip(output[scores >= threshold], scores[scores >= threshold]):
            cx, cy, w, h = row[:4]
            result.append(
                Detection(
                    (
                        float((cx - w / 2) / ratio),
                        float((cy - h / 2) / ratio),
                        float((cx + w / 2) / ratio),
                        float((cy + h / 2) / ratio),
                    ),
                    float(score),
                )
            )
        return non_max_suppression(result)


class TfliteSsdDetector:
    def __init__(self, path: Path, _device: str = "CPU") -> None:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ModuleNotFoundError:
            from tensorflow.lite.python.interpreter import Interpreter

        self.interpreter = Interpreter(model_path=str(path), num_threads=3)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.outputs = self.interpreter.get_output_details()
        _, self.height, self.width, _ = self.input["shape"]

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        height, width = frame.shape[:2]
        resized = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (self.width, self.height))
        self.interpreter.set_tensor(self.input["index"], resized[None].astype(np.uint8))
        self.interpreter.invoke()
        boxes = self.interpreter.get_tensor(self.outputs[0]["index"])[0]
        classes = self.interpreter.get_tensor(self.outputs[1]["index"])[0]
        scores = self.interpreter.get_tensor(self.outputs[2]["index"])[0]
        count = int(self.interpreter.get_tensor(self.outputs[3]["index"])[0])
        result = []
        for box, label, score in zip(boxes[:count], classes[:count], scores[:count]):
            if int(label) != 0 or float(score) < threshold:
                continue
            y1, x1, y2, x2 = box
            result.append(Detection((x1 * width, y1 * height, x2 * width, y2 * height), float(score)))
        return result


DETECTORS = {
    "openvino_ssd": OpenVinoSsdDetector,
    "openvino_yolox": OpenVinoYoloXDetector,
    "tflite_ssd": TfliteSsdDetector,
}


def benchmark_video(
    detector: Any, path: Path, *, threshold: float, sample_fps: float
) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or sample_fps
    every = max(1, round(source_fps / sample_fps))
    frame_index = sampled = total_detections = multi_detection_frames = 0
    presence: list[bool] = []
    latencies: list[float] = []
    scores: list[float] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % every:
                frame_index += 1
                continue
            started = time.perf_counter()
            detections = detector.detect(frame, threshold)
            latencies.append((time.perf_counter() - started) * 1000)
            presence.append(bool(detections))
            multi_detection_frames += len(detections) > 1
            scores.extend(item.score for item in detections)
            total_detections += len(detections)
            sampled += 1
            frame_index += 1
    finally:
        capture.release()
    ordered = sorted(latencies)
    return {
        "sampled_frames": sampled,
        "frames_with_person": sum(presence),
        "person_frame_rate": round(sum(presence) / sampled, 6) if sampled else 0,
        "detection_runs": detection_runs(presence),
        "total_detections": total_detections,
        "frames_with_multiple_detections": multi_detection_frames,
        "mean_score": round(sum(scores) / len(scores), 6) if scores else None,
        "latency_ms_p50": round(_percentile(ordered, 0.50), 3),
        "latency_ms_p95": round(_percentile(ordered, 0.95), 3),
    }


def _yolox_grids(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        hsize, wsize = height // stride, width // stride
        xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
        grids.append(np.stack((xv, yv), axis=2).reshape(-1, 2))
        strides.append(np.full((hsize * wsize, 1), stride))
    return np.concatenate(grids), np.concatenate(strides)


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1]) + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) - intersection
    return intersection / union if union else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    return values[min(len(values) - 1, math.floor((len(values) - 1) * quantile))]
