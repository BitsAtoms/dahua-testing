#!/usr/bin/env python3
"""Summarize DeepStream kitti-track-output-dir files without model dependencies."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrackObservation:
    frame_index: int
    label: str
    track_id: int
    box: tuple[float, float, float, float]
    confidence: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--label", default="Person")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def parse_track_directory(path: Path) -> list[TrackObservation]:
    observations: list[TrackObservation] = []
    for item in sorted(path.glob("*.txt")):
        frame_index = int(item.stem.rsplit("_", 1)[-1])
        observations.extend(parse_track_file(item, frame_index))
    return observations


def parse_track_file(path: Path, frame_index: int) -> list[TrackObservation]:
    observations: list[TrackObservation] = []
    pending_label: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        if parts[0][0].isalpha():
            pending_label = parts.pop(0)
            if not parts:
                continue
        if pending_label is None:
            raise ValueError(f"missing label in {path}")
        if len(parts) < 16:
            raise ValueError(f"invalid KITTI track row in {path}: {raw_line!r}")
        observations.append(
            TrackObservation(
                frame_index=frame_index,
                label=pending_label,
                track_id=int(parts[0]),
                box=tuple(float(value) for value in parts[4:8]),
                confidence=float(parts[-1]),
            )
        )
        pending_label = None
    if pending_label is not None:
        raise ValueError(f"label without values in {path}: {pending_label}")
    return observations


def build_report(
    observations: list[TrackObservation], *, label: str, file_count: int
) -> dict:
    selected = [item for item in observations if item.label == label]
    tracks: dict[int, list[TrackObservation]] = defaultdict(list)
    for item in selected:
        tracks[item.track_id].append(item)

    track_rows = []
    for track_id, items in sorted(tracks.items()):
        frames = [item.frame_index for item in items]
        centers_x = [(item.box[0] + item.box[2]) / 2 for item in items]
        gaps = [right - left for left, right in zip(frames, frames[1:])]
        track_rows.append(
            {
                "track_id": track_id,
                "observation_count": len(items),
                "first_frame": frames[0],
                "last_frame": frames[-1],
                "maximum_frame_gap": max(gaps, default=0),
                "minimum_center_x": round(min(centers_x), 3),
                "maximum_center_x": round(max(centers_x), 3),
                "first_center_x": round(centers_x[0], 3),
                "last_center_x": round(centers_x[-1], 3),
                "mean_confidence": round(
                    sum(item.confidence for item in items) / len(items), 6
                ),
                "observations": [asdict(item) for item in items],
            }
        )

    label_counts = Counter(item.label for item in observations)
    return {
        "schema_version": "deepstream_tracker_benchmark.v1",
        "source_file_count": file_count,
        "labels": dict(sorted(label_counts.items())),
        "selected_label": label,
        "track_count": len(track_rows),
        "tracks": track_rows,
    }


def main() -> int:
    args = parse_args()
    observations = parse_track_directory(args.input)
    report = build_report(
        observations,
        label=args.label,
        file_count=len(tuple(args.input.glob("*.txt"))),
    )
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        f"files={report['source_file_count']} "
        f"observations={report['labels'].get(args.label, 0)} "
        f"tracks={report['track_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
