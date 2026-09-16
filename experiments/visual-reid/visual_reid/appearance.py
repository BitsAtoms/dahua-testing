"""Weak clothing/color evidence for views unsupported by body re-ID."""

from __future__ import annotations

from dataclasses import dataclass

from .openvino_reid import body_crop
from .track_visuals import TrackVisual


@dataclass(frozen=True)
class AppearanceResult:
    quality: str
    vector: object | None


def color_descriptor(visual: TrackVisual) -> AppearanceResult:
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install experiments/visual-reid/requirements.txt in its venv"
        ) from error
    image = cv2.imread(str(visual.path), cv2.IMREAD_COLOR)
    if image is None:
        return AppearanceResult("decode_failed", None)
    crop = body_crop(image, visual.normalized_box)
    height, width = crop.shape[:2]
    if width < 64 or height < 64:
        return AppearanceResult("too_small", None)
    # Prefer the central torso area and reduce background influence.
    left, right = round(width * 0.15), round(width * 0.85)
    top, bottom = round(height * 0.15), round(height * 0.75)
    torso = crop[top:bottom, left:right]
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256])
    vector = histogram.reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return AppearanceResult("empty_histogram", None)
    return AppearanceResult("usable_weak", vector / norm)
