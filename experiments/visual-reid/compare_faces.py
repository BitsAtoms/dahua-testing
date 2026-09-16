#!/usr/bin/env python3
"""Compare native face crops for explicitly selected Dahua tracks."""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

from visual_reid import load_track_visuals
from visual_reid.openvino_reid import FaceEmbedder, cosine_similarity


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("track_ids", nargs="+")
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument("--device", default="CPU")
    args = parser.parse_args()
    visuals = load_track_visuals(
        args.receiver_database, args.track_ids, preferred_roles=("face",)
    )
    embedder = FaceEmbedder(
        ROOT
        / "models/face-reidentification-retail-0095/FP16"
        / "face-reidentification-retail-0095.xml",
        ROOT
        / "models/landmarks-regression-retail-0009/FP16"
        / "landmarks-regression-retail-0009.xml",
        args.device,
    )
    embeddings = {}
    for track_id in args.track_ids:
        visual = visuals.get(track_id)
        if visual is None:
            print(f"track={track_id} quality=no_face_media")
            continue
        result = embedder.embed(visual)
        if result.vector is not None:
            embeddings[track_id] = result.vector
        print(
            f"track={track_id} camera={visual.camera_id} role={visual.role} "
            f"crop={result.crop_size[0]}x{result.crop_size[1]} "
            f"quality={result.quality} details={result.details or {}}"
        )
    for left, right in combinations(args.track_ids, 2):
        if left not in embeddings or right not in embeddings:
            print(f"face_similarity=unavailable left={left} right={right}")
            continue
        print(
            f"face_similarity={cosine_similarity(embeddings[left], embeddings[right]):.6f} "
            f"left={left} right={right}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
