"""RF-DETR Medium COCO preprocessing and decoding, independent of ONNX Runtime."""

from __future__ import annotations

import cv2
import numpy as np

from .detector_benchmark import Detection
from .onnx_person_detector import OnnxRuntime


MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
PERSON_CLASS_ID = 1  # official sparse-ID COCO checkpoint; COCO person category ID is 1


def preprocess_rfdetr(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    if frame.ndim != 3 or frame.shape[2] != 3 or not all(x > 0 for x in frame.shape):
        raise ValueError("expected nonempty BGR image")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_LINEAR)
    normalized = (resized - MEAN) / STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32)


def decode_rfdetr(dets: np.ndarray, logits: np.ndarray,
                  source_shape: tuple[int, int], threshold: float) -> list[Detection]:
    if dets.ndim != 3 or dets.shape[0] != 1 or dets.shape[2] != 4:
        raise ValueError(f"unexpected RF-DETR boxes shape: {dets.shape}")
    if logits.ndim != 3 or logits.shape[:2] != dets.shape[:2] or logits.shape[2] != 91:
        raise ValueError(f"unexpected RF-DETR logits shape: {logits.shape}")
    if not np.isfinite(dets).all() or not np.isfinite(logits).all():
        raise ValueError("non-finite RF-DETR output")
    # Official COCO checkpoint keeps all 91 sparse-ID slots. Person is slot 1.
    person_logits = np.clip(logits[0, :, PERSON_CLASS_ID], -88.0, 88.0)
    scores = 1.0 / (1.0 + np.exp(-person_logits))
    source_h, source_w = source_shape
    result = []
    for (cx, cy, box_w, box_h), score in zip(dets[0][scores >= threshold], scores[scores >= threshold]):
        x1 = max(0., float((cx - box_w / 2) * source_w))
        y1 = max(0., float((cy - box_h / 2) * source_h))
        x2 = min(float(source_w), float((cx + box_w / 2) * source_w))
        y2 = min(float(source_h), float((cy + box_h / 2) * source_h))
        if x2 > x1 and y2 > y1:
            result.append(Detection((x1, y1, x2, y2), float(score)))
    return sorted(result, key=lambda item: item.score, reverse=True)


class RfDetrMediumPersonDetector:
    def __init__(self, runtime: OnnxRuntime):
        self.runtime = runtime
        if runtime.input_shape != [1, 3, 576, 576]:
            raise ValueError(f"unsupported RF-DETR Medium input: {runtime.input_shape}")
        if set(runtime.output_names) != {"dets", "labels"}:
            raise ValueError(f"unsupported RF-DETR output names: {runtime.output_names}")
        shapes = dict(zip(runtime.output_names, runtime.output_shapes))
        if shapes != {"dets": [1, 300, 4], "labels": [1, 300, 91]}:
            raise ValueError(f"unsupported RF-DETR outputs: {shapes}")
        self.height, self.width = runtime.input_shape[2:]

    def detect(self, frame: np.ndarray, threshold: float) -> list[Detection]:
        tensor = preprocess_rfdetr(frame, self.height, self.width)
        outputs = dict(zip(self.runtime.output_names, self.runtime.run(tensor)))
        return decode_rfdetr(outputs["dets"], outputs["labels"], frame.shape[:2], threshold)
