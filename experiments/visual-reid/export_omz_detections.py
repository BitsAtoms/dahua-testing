#!/usr/bin/env python3
"""Convert consensus sequences to Open Model Zoo demo detection JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--frame-index",
        choices=("source", "sampled"),
        default="source",
        help="Use source video indexes or contiguous sampled-video indexes.",
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    result = []
    for sequence in source["sequences"]:
        result.append(
            [
                {
                    "frame_id": int(
                        frame["source_frame_index"]
                        if args.frame_index == "source"
                        else frame["sampled_index"]
                    ),
                    "boxes": [item["box"] for item in frame["detections"]],
                    "scores": [item["score"] for item in frame["detections"]],
                }
                for frame in sequence["frames"]
            ]
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sources": len(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
