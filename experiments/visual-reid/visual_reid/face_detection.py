"""OpenVINO face discovery inside a track-correlated body crop."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path


ADAPTIVE_FACE_VALIDITY_VERSION = "adaptive-face-landmarks.v3"


@dataclass(frozen=True)
class FaceDetection:
    crop: object
    confidence: float
    box: tuple[int, int, int, int]
    validity_version: str = ADAPTIVE_FACE_VALIDITY_VERSION


@dataclass(frozen=True)
class FaceRejection:
    """A detector hit that must not become identity evidence."""

    crop: object
    confidence: float
    box: tuple[int, int, int, int]
    reason: str


class OpenVinoFaceDetector:
    def __init__(
        self,
        model_path: Path,
        device: str = "CPU",
        *,
        confidence_threshold: float = 0.15,
        minimum_size: int = 16,
        landmark_model_path: Path | None = None,
    ) -> None:
        if not 0 < confidence_threshold <= 1 or minimum_size <= 0:
            raise ValueError("face detector thresholds are invalid")
        try:
            import cv2
            import numpy as np
            import openvino as ov
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "install experiments/visual-reid/requirements.txt in its venv"
            ) from error
        self._cv2 = cv2
        self._np = np
        self.confidence_threshold = confidence_threshold
        self.minimum_size = minimum_size
        core = ov.Core()
        self._compiled = core.compile_model(core.read_model(model_path), device)
        self._output = self._compiled.output(0)
        self._landmarks = None
        self._landmark_output = None
        if landmark_model_path is not None:
            self._landmarks = core.compile_model(
                core.read_model(landmark_model_path), device
            )
            self._landmark_output = self._landmarks.output(0)

    def detect_best(self, image) -> FaceDetection | FaceRejection | None:
        if image is None or image.size == 0:
            return None
        tensor = self._cv2.resize(image, (300, 300), interpolation=self._cv2.INTER_LINEAR)
        tensor = tensor.transpose(2, 0, 1)[None].astype(self._np.float32)
        output = self._compiled([tensor])[self._output]
        boxes = parse_face_detections(
            output,
            image.shape[1],
            image.shape[0],
            confidence_threshold=self.confidence_threshold,
            minimum_size=self.minimum_size,
        )
        if not boxes:
            return None
        rejected: list[FaceRejection] = []
        for confidence, left, top, right, bottom in sorted(
            boxes,
            key=lambda item: (item[0], (item[3] - item[1]) * (item[4] - item[2])),
            reverse=True,
        ):
            crop = image[top:bottom, left:right]
            reason = adaptive_face_confidence_rejection_reason(confidence)
            if reason is None:
                reason = adaptive_face_rejection_reason(
                    right - left,
                    bottom - top,
                    container_width=image.shape[1],
                    container_height=image.shape[0],
                )
            if reason is None:
                reason = self._landmark_rejection_reason(crop)
            if reason is None:
                return FaceDetection(crop, confidence, (left, top, right, bottom))
            rejected.append(
                FaceRejection(
                    crop,
                    confidence,
                    (left, top, right, bottom),
                    reason,
                )
            )
        return rejected[0] if rejected else None

    def _landmark_rejection_reason(self, crop) -> str | None:
        if self._landmarks is None or self._landmark_output is None:
            return None
        tensor = self._cv2.resize(crop, (48, 48)).transpose(2, 0, 1)[None]
        tensor = tensor.astype(self._np.float32)
        points = self._landmarks([tensor])[self._landmark_output].reshape(5, 2)
        return adaptive_landmark_rejection_reason(points)


def adaptive_face_confidence_rejection_reason(
    confidence: float,
    *,
    minimum_confidence: float = 0.30,
) -> str | None:
    """Conservatively reject weak adaptive hits before identity embedding."""
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("face detector confidence must be between zero and one")
    if not 0 < minimum_confidence <= 1:
        raise ValueError("minimum face confidence must be between zero and one")
    if confidence < minimum_confidence:
        return "adaptive_face_low_confidence"
    return None


def adaptive_face_rejection_reason(
    width: int,
    height: int,
    *,
    minimum_width: int = 40,
    minimum_aspect_ratio: float = 0.45,
    maximum_aspect_ratio: float = 1.35,
    container_width: int | None = None,
    container_height: int | None = None,
    maximum_height_fraction: float = 0.75,
) -> str | None:
    """Validate RTSP-discovered faces before they reach FaceNet.

    This is deliberately scoped to adaptive crops. Native face crops keep their
    existing low-resolution path because their source detector has already
    associated them with a face event.
    """
    if width <= 0 or height <= 0:
        raise ValueError("face dimensions must be positive")
    if minimum_width <= 0 or not 0 < minimum_aspect_ratio <= maximum_aspect_ratio:
        raise ValueError("face validity thresholds are invalid")
    if (container_width is None) != (container_height is None):
        raise ValueError("both container dimensions are required together")
    if container_width is not None and (
        container_width <= 0
        or container_height is None
        or container_height <= 0
        or not 0 < maximum_height_fraction <= 1
    ):
        raise ValueError("face container thresholds are invalid")
    if width < minimum_width:
        return "adaptive_face_too_narrow"
    aspect_ratio = width / height
    if not minimum_aspect_ratio <= aspect_ratio <= maximum_aspect_ratio:
        return "adaptive_face_invalid_aspect"
    if (
        container_height is not None
        and height / container_height > maximum_height_fraction
    ):
        return "adaptive_face_oversized_in_body"
    return None


def adaptive_landmark_rejection_reason(
    points,
    *,
    minimum_eye_distance: float = 0.16,
    minimum_eye_balance: float = 0.35,
    maximum_eye_tilt: float = 0.30,
) -> str | None:
    """Reject detector hits whose predicted eye geometry is not face-like."""
    values = [[float(value) for value in point] for point in points]
    if len(values) != 5 or any(len(point) != 2 for point in values):
        raise ValueError("five two-dimensional landmarks are required")
    flat = [value for point in values for value in point]
    if any(not math.isfinite(value) or value < -0.05 or value > 1.05 for value in flat):
        return "adaptive_face_invalid_landmarks"
    left_eye, right_eye, nose = values[:3]
    eye_dx = right_eye[0] - left_eye[0]
    eye_dy = right_eye[1] - left_eye[1]
    eye_distance = math.hypot(eye_dx, eye_dy)
    if eye_distance < minimum_eye_distance:
        return "adaptive_face_landmarks_occluded"
    left_span = abs(nose[0] - left_eye[0])
    right_span = abs(right_eye[0] - nose[0])
    maximum_span = max(left_span, right_span)
    eye_balance = min(left_span, right_span) / maximum_span if maximum_span else 0.0
    eye_tilt = abs(eye_dy) / eye_distance
    if eye_balance < minimum_eye_balance or eye_tilt > maximum_eye_tilt:
        return "adaptive_face_landmark_geometry"
    return None


def parse_face_detections(
    output,
    image_width: int,
    image_height: int,
    *,
    confidence_threshold: float = 0.15,
    minimum_size: int = 16,
    padding: float = 0.20,
) -> list[tuple[float, int, int, int, int]]:
    """Convert the model's normalized SSD output into padded pixel boxes."""
    if image_width <= 0 or image_height <= 0 or minimum_size <= 0:
        raise ValueError("image and face dimensions must be positive")
    result: list[tuple[float, int, int, int, int]] = []
    for detection in output.reshape(-1, 7):
        image_id, label, confidence, x_min, y_min, x_max, y_max = (
            float(value) for value in detection
        )
        if image_id < 0:
            break
        if int(label) != 1 or confidence < confidence_threshold:
            continue
        left = max(0.0, min(1.0, x_min)) * image_width
        top = max(0.0, min(1.0, y_min)) * image_height
        right = max(0.0, min(1.0, x_max)) * image_width
        bottom = max(0.0, min(1.0, y_max)) * image_height
        width, height = right - left, bottom - top
        if width < minimum_size or height < minimum_size:
            continue
        left = max(0, round(left - width * padding))
        top = max(0, round(top - height * padding))
        right = min(image_width, round(right + width * padding))
        bottom = min(image_height, round(bottom + height * padding))
        if right > left and bottom > top:
            result.append((confidence, left, top, right, bottom))
    return result
