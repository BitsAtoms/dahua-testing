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
    FaceEmbedder,
    cosine_similarity,
)
from .track_visuals import load_track_visuals


class VisualEvaluator:
    def __init__(
        self,
        receiver_database: Path,
        experiment_root: Path,
        device: str = "CPU",
    ) -> None:
        self.receiver_database = receiver_database
        self.root = experiment_root
        version_input = {
            "evaluator": "visual-evaluator.v1",
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
        self._face = FaceEmbedder(
            self.root
            / "models/face-reidentification-retail-0095/FP16"
            / "face-reidentification-retail-0095.xml",
            self.root
            / "models/landmarks-regression-retail-0009/FP16"
            / "landmarks-regression-retail-0009.xml",
            device,
        )
        self._bodies: dict[str, EmbeddingResult] = {}
        self._faces: dict[str, EmbeddingResult] = {}
        self._colors: dict[str, AppearanceResult] = {}

    def prepare(self, track_ids: list[str]) -> None:
        pending_body = [track for track in track_ids if track not in self._bodies]
        body_visuals = load_track_visuals(self.receiver_database, pending_body)
        for track_id in pending_body:
            visual = body_visuals.get(track_id)
            if visual is None:
                self._bodies[track_id] = EmbeddingResult("missing", None, (0, 0))
                self._colors[track_id] = AppearanceResult("missing", None)
                continue
            self._bodies[track_id] = self._body.embed(visual)
            self._colors[track_id] = color_descriptor(visual)

        pending_face = [track for track in track_ids if track not in self._faces]
        face_visuals = load_track_visuals(
            self.receiver_database, pending_face, preferred_roles=("face",)
        )
        for track_id in pending_face:
            visual = face_visuals.get(track_id)
            self._faces[track_id] = (
                self._face.embed(visual)
                if visual is not None
                else EmbeddingResult("missing", None, (0, 0))
            )

    def evaluate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        origin = candidate["origin_track_id"]
        destination = candidate["destination_track_id"]
        self.prepare([origin, destination])
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
    return {
        "score": score,
        "origin_quality": origin.quality,
        "destination_quality": destination.quality,
    }
