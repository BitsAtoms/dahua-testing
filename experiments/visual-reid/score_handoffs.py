#!/usr/bin/env python3
"""Attach ephemeral visual evidence to recent spatial-temporal candidates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from visual_reid import load_track_visuals
from visual_reid.appearance import AppearanceResult, color_descriptor
from visual_reid.openvino_reid import (
    BodyEmbedder,
    EmbeddingResult,
    FaceEmbedder,
    cosine_similarity,
)


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Candidate:
    origin_track_id: str
    destination_track_id: str
    origin_camera: str
    destination_camera: str
    timing_score: float
    gap_seconds: float
    observed_at: str


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--tracking-database",
        type=Path,
        default=Path("runtime/tracking-engine/tracking.sqlite3"),
    )
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument("--device", default="CPU")
    args = parser.parse_args()
    if args.limit <= 0:
        raise ValueError("limit must be positive")
    candidates = _load_candidates(args.tracking_database, args.limit)
    if not candidates:
        print("handoff_candidates=0")
        return 0
    track_ids = list(
        dict.fromkeys(
            track_id
            for candidate in candidates
            for track_id in (
                candidate.origin_track_id,
                candidate.destination_track_id,
            )
        )
    )
    body_visuals = load_track_visuals(args.receiver_database, track_ids)
    face_visuals = load_track_visuals(
        args.receiver_database, track_ids, preferred_roles=("face",)
    )
    body_embedder = BodyEmbedder(
        ROOT
        / "models/person-reidentification-retail-0287/FP16"
        / "person-reidentification-retail-0287.xml",
        args.device,
    )
    face_embedder = FaceEmbedder(
        ROOT
        / "models/face-reidentification-retail-0095/FP16"
        / "face-reidentification-retail-0095.xml",
        ROOT
        / "models/landmarks-regression-retail-0009/FP16"
        / "landmarks-regression-retail-0009.xml",
        args.device,
    )
    bodies: dict[str, EmbeddingResult] = {}
    faces: dict[str, EmbeddingResult] = {}
    colors: dict[str, AppearanceResult] = {}
    for track_id, visual in body_visuals.items():
        bodies[track_id] = body_embedder.embed(visual)
        colors[track_id] = color_descriptor(visual)
    for track_id, visual in face_visuals.items():
        faces[track_id] = face_embedder.embed(visual)

    print(f"handoff_candidates={len(candidates)}")
    for candidate in candidates:
        origin, destination = (
            candidate.origin_track_id,
            candidate.destination_track_id,
        )
        face_score = _similarity(faces.get(origin), faces.get(destination))
        body_score = _similarity(bodies.get(origin), bodies.get(destination))
        color_score = _similarity(colors.get(origin), colors.get(destination))
        print(
            f"at={candidate.observed_at} "
            f"from={candidate.origin_camera} to={candidate.destination_camera} "
            f"gap={candidate.gap_seconds:.3f}s timing={candidate.timing_score:.3f} "
            f"face={_score(face_score)} body={_score(body_score)} "
            f"color={_score(color_score)} "
            f"face_quality={_quality(faces.get(origin))}/{_quality(faces.get(destination))} "
            f"body_quality={_quality(bodies.get(origin))}/{_quality(bodies.get(destination))}"
        )
    return 0


def _load_candidates(path: Path, limit: int) -> list[Candidate]:
    if not path.is_file():
        raise FileNotFoundError(f"tracking database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT h.origin_track_id, h.destination_track_id,
                   o.camera_id, d.camera_id, h.score, h.gap_seconds,
                   h.observed_at
            FROM handoff_candidates h
            JOIN local_tracks o ON o.track_id = h.origin_track_id
            JOIN local_tracks d ON d.track_id = h.destination_track_id
            ORDER BY h.observed_us DESC, h.score DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [Candidate(*row) for row in rows]
    finally:
        connection.close()


def _similarity(left, right) -> float | None:
    if left is None or right is None:
        return None
    if left.vector is None or right.vector is None:
        return None
    return cosine_similarity(left.vector, right.vector)


def _score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _quality(value) -> str:
    return "missing" if value is None else value.quality


if __name__ == "__main__":
    raise SystemExit(main())
