"""In-memory embedding cache and explainable candidate evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .appearance import AppearanceResult, color_descriptor
from .fusion import (
    BODY_WEIGHT,
    COLOR_WEIGHT,
    FACE_WEIGHT,
    FUSION_VERSION,
    TIMING_WEIGHT,
    rank_evidence,
)
from .openvino_reid import (
    BodyEmbedder,
    EmbeddingResult,
    FaceNetEmbedder,
    cosine_similarity,
)
from .templates import TemplateObservation, build_template
from .track_visuals import TrackVisual, load_track_visual_sets


class VisualEvaluator:
    def __init__(
        self,
        receiver_database: Path,
        experiment_root: Path,
        device: str = "CPU",
        adaptive_media_database: Path | None = None,
    ) -> None:
        self.receiver_database = receiver_database
        self.root = experiment_root
        self.adaptive_media_database = adaptive_media_database
        version_input = {
            "evaluator": "visual-evaluator.v8-adaptive-body-live-only-v2",
            "fusion": FUSION_VERSION,
            "weights": [TIMING_WEIGHT, FACE_WEIGHT, BODY_WEIGHT, COLOR_WEIGHT],
            "model_manifest_sha256": hashlib.sha256(
                (self.root / "model-manifest.json").read_bytes()
            ).hexdigest(),
        }
        self.model_version = hashlib.sha256(
            json.dumps(version_input, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        self._body = BodyEmbedder(
            self.root
            / "models/person-reidentification-retail-0287/FP16"
            / "person-reidentification-retail-0287.xml",
            device,
        )
        self._face = FaceNetEmbedder(
            self.root
            / "models/facenet-small-v1/facenet.tflite",
            self.root
            / "models/landmarks-regression-retail-0009/FP16"
            / "landmarks-regression-retail-0009.xml",
            device,
        )
        self._bodies: dict[str, EmbeddingResult] = {}
        self._faces: dict[str, EmbeddingResult] = {}
        self._colors: dict[str, AppearanceResult] = {}
        self._track_revisions: dict[str, int] = {}

    def prepare(self, track_revisions: dict[str, int]) -> None:
        pending = [
            track
            for track, revision in track_revisions.items()
            if self._track_revisions.get(track) != revision
        ]
        body_visuals = load_track_visual_sets(
            self.receiver_database,
            pending,
            adaptive_media_database=self.adaptive_media_database,
        )
        for track_id in pending:
            visuals = body_visuals.get(track_id, [])
            if not visuals:
                self._bodies[track_id] = EmbeddingResult("missing", None, (0, 0))
                self._colors[track_id] = AppearanceResult("missing", None)
            else:
                self._bodies[track_id] = _embedding_template(
                    track_id, visuals, self._body.embed, "body"
                )
                self._colors[track_id] = _appearance_template(
                    track_id, visuals
                )

        face_visuals = load_track_visual_sets(
            self.receiver_database,
            pending,
            preferred_roles=("face",),
            adaptive_media_database=self.adaptive_media_database,
        )
        for track_id in pending:
            visuals = face_visuals.get(track_id, [])
            self._faces[track_id] = (
                _embedding_template(track_id, visuals, self._face.embed, "face")
                if visuals
                else EmbeddingResult("missing", None, (0, 0))
            )
            self._track_revisions[track_id] = track_revisions[track_id]

    def evaluate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        origin = candidate["origin_track_id"]
        destination = candidate["destination_track_id"]
        self.prepare(
            {
                origin: int(candidate["origin_revision_us"]),
                destination: int(candidate["destination_revision_us"]),
            }
        )
        face_score = _similarity(self._faces[origin], self._faces[destination])
        body_score = _similarity(self._bodies[origin], self._bodies[destination])
        color_score = _similarity(self._colors[origin], self._colors[destination])
        fusion = rank_evidence(
            candidate["timing_score"], face_score, body_score, color_score
        )
        return {
            "schema_version": "visual_handoff_evidence.v1",
            "candidate_id": candidate["candidate_id"],
            "candidate_observed_at": candidate["observed_at"],
            "candidate_observed_us": candidate["observed_us"],
            "candidate_fingerprint": candidate_fingerprint(candidate),
            "origin_track_id": origin,
            "destination_track_id": destination,
            "origin_revision_us": candidate["origin_revision_us"],
            "destination_revision_us": candidate["destination_revision_us"],
            "model_version": self.model_version,
            "ranking_not_identity_probability": True,
            "identity_decision": None,
            "ranking_score": fusion.ranking_score,
            "visual_coverage": fusion.visual_coverage,
            "available_modalities": list(fusion.available_modalities),
            "signals": {
                "timing": {"score": candidate["timing_score"]},
                "face": _signal(face_score, self._faces[origin], self._faces[destination]),
                "body": _signal(body_score, self._bodies[origin], self._bodies[destination]),
                "color": _signal(color_score, self._colors[origin], self._colors[destination]),
            },
        }


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    """Identify changes in the mutable timing projection for one candidate."""
    wire = json.dumps(
        {
            "candidate_id": candidate["candidate_id"],
            "timing_score": candidate["timing_score"],
            "gap_seconds": candidate["gap_seconds"],
            "observed_us": candidate["observed_us"],
            "origin_revision_us": candidate.get("origin_revision_us", 0),
            "destination_revision_us": candidate.get("destination_revision_us", 0),
            "origin_adaptive_revision_us": candidate.get(
                "origin_adaptive_revision_us", 0
            ),
            "destination_adaptive_revision_us": candidate.get(
                "destination_adaptive_revision_us", 0
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()[:16]


def _similarity(left, right) -> float | None:
    if left.vector is None or right.vector is None:
        return None
    return round(cosine_similarity(left.vector, right.vector), 6)


def _signal(score: float | None, origin, destination) -> dict[str, Any]:
    signal = {
        "score": score,
        "origin_quality": origin.quality,
        "destination_quality": destination.quality,
    }
    if getattr(origin, "details", None):
        signal["origin_template"] = origin.details
    if getattr(destination, "details", None):
        signal["destination_template"] = destination.details
    return signal


def _embedding_template(track_id, visuals, embed, modality) -> EmbeddingResult:
    results = [(visual, embed(visual)) for visual in visuals]
    usable = [(visual, result) for visual, result in results if result.vector is not None]
    if not usable:
        return results[-1][1]
    observations = [
        TemplateObservation(
            f"{track_id}:{modality}:{index}:{visual.path}",
            visual.observed_us or index + 1,
            result.vector,
            visual.quality_weight * (0.65 if result.quality == "usable_low_resolution" else 1.0),
        )
        for index, (visual, result) in enumerate(usable)
    ]
    template = build_template(observations)
    widths = [result.crop_size[0] for _visual, result in usable]
    heights = [result.crop_size[1] for _visual, result in usable]
    return EmbeddingResult(
        "usable_multiframe" if len(template.observation_ids) > 1 else usable[-1][1].quality,
        template.vector,
        (max(widths), max(heights)),
        {
            "observations": float(len(template.observation_ids)),
            "rejected": float(len(template.rejected_ids)),
            "dispersion": template.dispersion,
        },
    )


def _appearance_template(track_id: str, visuals: list[TrackVisual]) -> AppearanceResult:
    results = [(visual, color_descriptor(visual)) for visual in visuals]
    usable = [(visual, result) for visual, result in results if result.vector is not None]
    if not usable:
        return results[-1][1]
    observations = [
        TemplateObservation(
            f"{track_id}:color:{index}:{visual.path}",
            visual.observed_us or index + 1,
            result.vector,
            visual.quality_weight,
        )
        for index, (visual, result) in enumerate(usable)
    ]
    template = build_template(observations)
    return AppearanceResult(
        "usable_multiframe" if len(template.observation_ids) > 1 else usable[-1][1].quality,
        template.vector,
        {
            "observations": float(len(template.observation_ids)),
            "rejected": float(len(template.rejected_ids)),
            "dispersion": template.dispersion,
        },
    )
