"""Source-neutral image quality measurements for adaptive evidence capture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Modality = Literal["face", "body"]


@dataclass(frozen=True)
class QualityAssessment:
    modality: Modality
    score: float
    usable_for_embedding: bool
    strong: bool
    width: int
    height: int
    brightness: float
    contrast: float
    sharpness: float
    reasons: tuple[str, ...]


def assess_image_quality(
    image,
    modality: Modality,
    *,
    detector_confidence: float | None = None,
    geometry_warning: bool = False,
    occluded: bool = False,
) -> QualityAssessment:
    """Measure capture quality without making an identity decision.

    The score is an experimental capture-priority signal, not a calibrated
    probability. Thresholds deliberately mirror the current embedding input
    constraints and can later be replaced by a learned FIQA provider.
    """
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install experiments/visual-reid/requirements.txt in its venv"
        ) from error
    if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
        raise ValueError("image must be a decoded grayscale or color array")
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if detector_confidence is not None and not 0 <= detector_confidence <= 1:
        raise ValueError("detector_confidence must be between zero and one")

    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(image.shape) == 3
        else image
    )
    brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    minimum = min(width, height)
    if modality == "face":
        minimum_dimension = 24
        strong_dimension = 80
        shape_supported = True
    elif modality == "body":
        minimum_dimension = 64
        strong_dimension = 128
        shape_supported = height / width >= 1.0
    else:
        raise ValueError(f"unsupported modality: {modality}")

    size_score = _unit(minimum / strong_dimension)
    sharpness_score = _unit(sharpness / 100.0)
    exposure_score = _unit(1.0 - abs(brightness - 127.5) / 127.5)
    contrast_score = _unit(contrast / 50.0)
    pose_score = 0.35 if geometry_warning else 1.0
    confidence_score = detector_confidence if detector_confidence is not None else 0.7
    score = (
        0.35 * size_score
        + 0.25 * sharpness_score
        + 0.15 * exposure_score
        + 0.10 * contrast_score
        + 0.10 * pose_score
        + 0.05 * confidence_score
    )
    reasons: list[str] = []
    if minimum < minimum_dimension:
        reasons.append("too_small")
    elif minimum < strong_dimension:
        reasons.append("low_resolution")
    if sharpness < 40:
        reasons.append("blurred")
    if brightness < 35:
        reasons.append("underexposed")
    elif brightness > 220:
        reasons.append("overexposed")
    if contrast < 18:
        reasons.append("low_contrast")
    if geometry_warning:
        reasons.append("pose_warning")
    if not shape_supported:
        reasons.append("unsupported_pose")
    if occluded:
        reasons.append("occluded")

    usable = (
        minimum >= minimum_dimension
        and shape_supported
        and not occluded
        and score >= 0.35
    )
    strong = (
        minimum >= strong_dimension
        and shape_supported
        and not geometry_warning
        and not occluded
        and sharpness >= 40
        and 35 <= brightness <= 220
        and contrast >= 18
        and score >= 0.65
    )
    return QualityAssessment(
        modality=modality,
        score=round(score, 6),
        usable_for_embedding=usable,
        strong=strong,
        width=width,
        height=height,
        brightness=round(brightness, 3),
        contrast=round(contrast, 3),
        sharpness=round(sharpness, 3),
        reasons=tuple(reasons),
    )


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
