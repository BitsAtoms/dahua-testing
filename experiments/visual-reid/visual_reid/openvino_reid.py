"""OpenVINO body embedding backend for controlled local evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .track_visuals import TrackVisual


# Keep the local experiment deterministic and prevent optional telemetry setup.
os.environ.setdefault("CI", "true")


@dataclass(frozen=True)
class EmbeddingResult:
    quality: str
    vector: object | None
    crop_size: tuple[int, int]
    details: dict[str, float] | None = None


class BodyEmbedder:
    def __init__(self, model_path: Path, device: str = "CPU") -> None:
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
        core = ov.Core()
        model = core.read_model(model_path)
        self._compiled = core.compile_model(model, device)
        self._input = self._compiled.input(0)
        self._output = self._compiled.output(0)

    def embed(self, visual: TrackVisual):
        cv2 = self._cv2
        np = self._np
        image = cv2.imread(str(visual.path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode image: {visual.path}")
        crop = body_crop(image, visual.normalized_box)
        if crop.size == 0:
            raise ValueError(f"empty body crop: {visual.track_id}")
        crop_size = (crop.shape[1], crop.shape[0])
        if crop.shape[1] < 64 or crop.shape[0] < 128:
            return EmbeddingResult("too_small", None, crop_size)
        if crop.shape[0] / crop.shape[1] < 1.0:
            return EmbeddingResult("unsupported_overhead_pose", None, crop_size)
        resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR)
        tensor = resized.transpose(2, 0, 1)[None].astype(np.float32)
        vector = self._compiled([tensor])[self._output].reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise ValueError(f"zero embedding: {visual.track_id}")
        return EmbeddingResult("usable", vector / norm, crop_size)


class FaceEmbedder:
    REFERENCE_LANDMARKS = (
        (0.31556875, 0.4615741071428571),
        (0.6826229166666667, 0.4615741071428571),
        (0.5002625, 0.6405053571428571),
        (0.349471875, 0.8246919642857142),
        (0.6534364583333333, 0.8246919642857142),
    )

    def __init__(
        self, face_model: Path, landmark_model: Path, device: str = "CPU"
    ) -> None:
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
        core = ov.Core()
        landmarks = core.compile_model(core.read_model(landmark_model), device)
        face = core.compile_model(core.read_model(face_model), device)
        self._landmarks = landmarks
        self._landmark_output = landmarks.output(0)
        self._face = face
        self._face_output = face.output(0)

    def embed(self, visual: TrackVisual) -> EmbeddingResult:
        cv2 = self._cv2
        np = self._np
        image = cv2.imread(str(visual.path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode image: {visual.path}")
        quality, aligned, crop_size, details = _align_face(
            image,
            self._landmarks,
            self._landmark_output,
            self.REFERENCE_LANDMARKS,
            minimum_size=80,
            output_size=128,
            cv2=cv2,
            np=np,
        )
        if aligned is None:
            return EmbeddingResult(quality, None, crop_size, details)
        tensor = aligned.transpose(2, 0, 1)[None].astype(np.float32)
        vector = self._face([tensor])[self._face_output].reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return EmbeddingResult("zero_embedding", None, crop_size, details)
        return EmbeddingResult("usable", vector / norm, crop_size, details)


class FaceNetEmbedder:
    """Anonymous FaceNet embeddings, including low-resolution Frigate crops."""

    REFERENCE_LANDMARKS = FaceEmbedder.REFERENCE_LANDMARKS

    def __init__(
        self, face_model: Path, landmark_model: Path, device: str = "CPU"
    ) -> None:
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
        core = ov.Core()
        landmarks = core.compile_model(core.read_model(landmark_model), device)
        face = core.compile_model(core.read_model(face_model), device)
        self._landmarks = landmarks
        self._landmark_output = landmarks.output(0)
        self._face = face
        self._face_output = face.output(0)

    def embed(self, visual: TrackVisual) -> EmbeddingResult:
        cv2 = self._cv2
        np = self._np
        image = cv2.imread(str(visual.path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode image: {visual.path}")
        quality, aligned, crop_size, details = _align_face(
            image,
            self._landmarks,
            self._landmark_output,
            self.REFERENCE_LANDMARKS,
            minimum_size=24,
            output_size=160,
            reject_profile=False,
            cv2=cv2,
            np=np,
        )
        if aligned is None:
            return EmbeddingResult(quality, None, crop_size, details)
        tensor = facenet_tensor(aligned, cv2, np)
        vector = self._face([tensor])[self._face_output].reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return EmbeddingResult("zero_embedding", None, crop_size, details)
        quality = "usable" if min(crop_size) >= 80 else "usable_low_resolution"
        return EmbeddingResult(quality, vector / norm, crop_size, details)


def facenet_tensor(aligned_bgr, cv2, np):
    """Convert an aligned BGR crop to FaceNet's NHWC RGB input."""
    rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB)
    return ((rgb.astype(np.float32) / 127.5) - 1.0)[None]


def _align_face(
    image,
    landmarks,
    landmark_output,
    reference_landmarks,
    *,
    minimum_size: int,
    output_size: int,
    reject_profile: bool = True,
    cv2,
    np,
):
    height, width = image.shape[:2]
    crop_size = (width, height)
    if min(width, height) < minimum_size:
        return "too_small", None, crop_size, None
    landmark_input = cv2.resize(image, (48, 48)).transpose(2, 0, 1)[None]
    landmark_input = landmark_input.astype(np.float32)
    points = landmarks([landmark_input])[landmark_output]
    points = points.reshape(5, 2).astype(np.float32)
    if not np.isfinite(points).all() or (points < -0.05).any() or (points > 1.05).any():
        return "invalid_landmarks", None, crop_size, None
    eye_distance = float(np.linalg.norm(points[0] - points[1]))
    if eye_distance < 0.16:
        return "profile_or_occluded", None, crop_size, None
    nose = points[2]
    eye_left_span = abs(float(nose[0] - points[0][0]))
    eye_right_span = abs(float(points[1][0] - nose[0]))
    mouth_left_span = abs(float(nose[0] - points[3][0]))
    mouth_right_span = abs(float(points[4][0] - nose[0]))
    eye_balance = _balance(eye_left_span, eye_right_span)
    mouth_balance = _balance(mouth_left_span, mouth_right_span)
    eye_tilt = abs(float(points[0][1] - points[1][1])) / eye_distance
    details = {
        "eye_balance": round(eye_balance, 4),
        "mouth_balance": round(mouth_balance, 4),
        "eye_tilt": round(eye_tilt, 4),
    }
    # Eye geometry is stable enough for a conservative profile gate. Mouth
    # landmarks become unreliable with beard, expression or partial light,
    # so retain that metric for diagnostics without rejecting on it.
    geometry_warning = eye_balance < 0.35 or eye_tilt > 0.35
    if geometry_warning and reject_profile:
        return "profile_or_occluded", None, crop_size, details
    if geometry_warning:
        details["geometry_warning"] = 1.0
    source = points * np.array([width, height], dtype=np.float32)
    target = np.array(reference_landmarks, dtype=np.float32) * output_size
    transform, _inliers = cv2.estimateAffinePartial2D(
        source, target, method=cv2.LMEDS
    )
    if transform is None:
        return "alignment_failed", None, crop_size, details
    aligned = cv2.warpAffine(
        image, transform, (output_size, output_size), flags=cv2.INTER_LINEAR
    )
    return "usable", aligned, crop_size, details


def cosine_similarity(left, right) -> float:
    import numpy as np

    return float(np.dot(left, right))


def _balance(left: float, right: float) -> float:
    maximum = max(left, right)
    return min(left, right) / maximum if maximum else 0.0


def body_crop(image, box: dict[str, float] | None):
    if box is None:
        return image
    height, width = image.shape[:2]
    x_min = max(0.0, min(1.0, float(box["x_min"])))
    y_min = max(0.0, min(1.0, float(box["y_min"])))
    x_max = max(0.0, min(1.0, float(box["x_max"])))
    y_max = max(0.0, min(1.0, float(box["y_max"])))
    box_width = x_max - x_min
    box_height = y_max - y_min
    x_min = max(0.0, x_min - box_width * 0.03)
    x_max = min(1.0, x_max + box_width * 0.03)
    y_min = max(0.0, y_min - box_height * 0.03)
    y_max = min(1.0, y_max + box_height * 0.03)
    left, top = round(x_min * width), round(y_min * height)
    right, bottom = round(x_max * width), round(y_max * height)
    return image[top:bottom, left:right]
