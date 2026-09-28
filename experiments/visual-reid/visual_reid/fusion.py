"""Transparent provisional ranking of heterogeneous handoff evidence."""

from __future__ import annotations

from dataclasses import dataclass


TIMING_WEIGHT = 0.35
FACE_WEIGHT = 0.40
BODY_WEIGHT = 0.20
COLOR_WEIGHT = 0.05
VISUAL_WEIGHT = FACE_WEIGHT + BODY_WEIGHT + COLOR_WEIGHT
FUSION_VERSION = "fusion.v1"


@dataclass(frozen=True)
class FusionResult:
    ranking_score: float
    visual_coverage: float
    available_modalities: tuple[str, ...]


def rank_evidence(
    timing: float,
    face: float | None,
    body: float | None,
    color: float | None,
) -> FusionResult:
    """Rank support without claiming an identity probability or decision."""
    values = {"face": face, "body": body, "color": color}
    weights = {"face": FACE_WEIGHT, "body": BODY_WEIGHT, "color": COLOR_WEIGHT}
    available = tuple(name for name, value in values.items() if value is not None)
    visual_weight = sum(weights[name] for name in available)
    score = TIMING_WEIGHT * _unit(timing)
    for name in available:
        score += weights[name] * _unit(values[name])
    return FusionResult(
        ranking_score=round(score, 6),
        visual_coverage=round(visual_weight / VISUAL_WEIGHT, 6),
        available_modalities=available,
    )


def _unit(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))
