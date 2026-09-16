#!/usr/bin/env python3
"""Compare retained body appearance for explicitly selected tracks."""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

from visual_reid import load_track_visuals
from visual_reid.appearance import color_descriptor
from visual_reid.openvino_reid import BodyEmbedder, cosine_similarity


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("track_ids", nargs="+")
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=(
            ROOT
            / "models/person-reidentification-retail-0287/FP16"
            / "person-reidentification-retail-0287.xml"
        ),
    )
    parser.add_argument("--device", default="CPU")
    args = parser.parse_args()
    visuals = load_track_visuals(args.receiver_database, args.track_ids)
    missing = [track_id for track_id in args.track_ids if track_id not in visuals]
    if missing:
        raise ValueError(f"tracks without usable media: {', '.join(missing)}")
    embedder = BodyEmbedder(args.model, args.device)
    embeddings = {}
    colors = {}
    for track_id in args.track_ids:
        visual = visuals[track_id]
        result = embedder.embed(visual)
        if result.vector is not None:
            embeddings[track_id] = result.vector
        appearance = color_descriptor(visual)
        if appearance.vector is not None:
            colors[track_id] = appearance.vector
        print(
            f"track={track_id} source={visual.source_type} "
            f"camera={visual.camera_id} role={visual.role} "
            f"crop={result.crop_size[0]}x{result.crop_size[1]} "
            f"body_quality={result.quality} color_quality={appearance.quality}"
        )
    for left, right in combinations(args.track_ids, 2):
        if left not in embeddings or right not in embeddings:
            print(f"body_similarity=unavailable left={left} right={right}")
        else:
            similarity = cosine_similarity(embeddings[left], embeddings[right])
            print(f"body_similarity={similarity:.6f} left={left} right={right}")
        if left not in colors or right not in colors:
            print(f"color_similarity=unavailable left={left} right={right}")
        else:
            similarity = cosine_similarity(colors[left], colors[right])
            print(f"color_similarity={similarity:.6f} left={left} right={right}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
