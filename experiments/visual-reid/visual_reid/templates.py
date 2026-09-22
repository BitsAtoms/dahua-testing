"""Robust in-memory templates built from multiple anonymous embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TemplateObservation:
    observation_id: str
    observed_us: int
    vector: object
    quality_weight: float


@dataclass(frozen=True)
class TrackTemplate:
    vector: object
    observation_ids: tuple[str, ...]
    rejected_ids: tuple[str, ...]
    dispersion: float
    effective_weight: float


@dataclass(frozen=True)
class TemplateComparison:
    similarity: float
    reliability: float


def build_template(
    observations: list[TemplateObservation],
    *,
    max_observations: int = 5,
) -> TrackTemplate:
    """Build a normalized quality-weighted centroid after robust outlier removal."""
    if max_observations <= 0:
        raise ValueError("max_observations must be positive")
    if not observations:
        raise ValueError("at least one observation is required")
    import numpy as np

    prepared: list[tuple[TemplateObservation, object]] = []
    dimensions: set[int] = set()
    identifiers: set[str] = set()
    for item in observations:
        if not item.observation_id or item.observation_id in identifiers:
            raise ValueError("observation identifiers must be unique and non-empty")
        if item.observed_us <= 0:
            raise ValueError("observed_us must be positive")
        if not isfinite(item.quality_weight) or item.quality_weight <= 0:
            raise ValueError("quality_weight must be finite and positive")
        vector = np.asarray(item.vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or norm == 0:
            raise ValueError("embedding vectors must be finite and non-zero")
        identifiers.add(item.observation_id)
        dimensions.add(int(vector.size))
        prepared.append((item, vector / norm))
    if len(dimensions) != 1:
        raise ValueError("embedding dimensions must match")

    # Prefer quality first, then recency, while keeping the operation bounded.
    prepared = sorted(
        prepared,
        key=lambda pair: (pair[0].quality_weight, pair[0].observed_us),
        reverse=True,
    )[:max_observations]
    matrix = np.stack([pair[1] for pair in prepared])
    similarities = matrix @ matrix.T
    medoid_index = int(np.argmax(similarities.mean(axis=1)))
    medoid_scores = similarities[medoid_index]
    median = float(np.median(medoid_scores))
    deviation = float(np.median(np.abs(medoid_scores - median)))
    # This is a within-track consistency rule, not an identity threshold.
    cutoff = median - max(0.10, 2.5 * deviation)
    keep = [index for index, score in enumerate(medoid_scores) if score >= cutoff]
    if not keep:
        keep = [medoid_index]
    selected = [prepared[index] for index in keep]
    rejected = [
        prepared[index][0].observation_id
        for index in range(len(prepared))
        if index not in keep
    ]
    weights = np.asarray(
        [min(1.0, max(0.05, pair[0].quality_weight)) for pair in selected],
        dtype=np.float32,
    )
    vectors = np.stack([pair[1] for pair in selected])
    centroid = (vectors * weights[:, None]).sum(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm == 0:
        centroid = selected[0][1]
    else:
        centroid = centroid / norm
    dispersion = 1.0 - float((vectors @ centroid).mean())
    return TrackTemplate(
        vector=centroid.astype(np.float32),
        observation_ids=tuple(pair[0].observation_id for pair in selected),
        rejected_ids=tuple(rejected),
        dispersion=round(max(0.0, dispersion), 6),
        effective_weight=round(float(weights.sum()), 6),
    )


def compare_templates(left: TrackTemplate, right: TrackTemplate) -> TemplateComparison:
    import numpy as np

    similarity = float(np.dot(left.vector, right.vector))
    consistency = max(0.0, 1.0 - (left.dispersion + right.dispersion) / 2.0)
    sample_support = min(
        1.0,
        (len(left.observation_ids) * len(right.observation_ids)) ** 0.5 / 3.0,
    )
    return TemplateComparison(
        similarity=round(similarity, 6),
        reliability=round(consistency * sample_support, 6),
    )
