#!/usr/bin/env python3
"""Measure whether one detector independently confirms DeepStream tracks."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path

from analyze_deepstream_tracks import TrackObservation, parse_track_directory


@dataclass(frozen=True)
class Detection:
    frame_index: int
    label: str
    box: tuple[float, float, float, float]
    confidence: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--track-label", default="Person")
    parser.add_argument("--detection-label", default="Person")
    parser.add_argument("--iou", type=float, default=0.3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def parse_detection_directory(path: Path) -> list[Detection]:
    detections: list[Detection] = []
    for item in sorted(path.glob("*.txt")):
        frame_index = int(item.stem.rsplit("_", 1)[-1])
        lines = [line.strip() for line in item.read_text(encoding="utf-8").splitlines()]
        index = 0
        while index < len(lines):
            if not lines[index]:
                index += 1
                continue
            label = lines[index]
            index += 1
            if index >= len(lines):
                raise ValueError(f"missing detection row in {item}")
            values = lines[index].split()
            index += 1
            if len(values) < 15:
                raise ValueError(f"invalid detection row in {item}: {values!r}")
            detections.append(
                Detection(
                    frame_index=frame_index,
                    label=label,
                    box=tuple(float(value) for value in values[3:7]),
                    confidence=float(values[14]),
                )
            )
    return detections


def intersection_over_union(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def build_confirmation_report(
    tracks: list[TrackObservation],
    detections: list[Detection],
    *,
    track_label: str,
    detection_label: str,
    iou_threshold: float,
) -> dict[str, object]:
    detections_by_frame: dict[int, list[Detection]] = defaultdict(list)
    for detection in detections:
        if detection.label == detection_label:
            detections_by_frame[detection.frame_index].append(detection)

    tracks_by_id: dict[int, list[TrackObservation]] = defaultdict(list)
    for observation in tracks:
        if observation.label == track_label:
            tracks_by_id[observation.track_id].append(observation)

    rows = []
    for track_id, observations in sorted(tracks_by_id.items()):
        observations.sort(key=lambda item: item.frame_index)
        confirmations: list[tuple[int, float]] = []
        for observation in observations:
            maximum_iou = max(
                (
                    intersection_over_union(observation.box, detection.box)
                    for detection in detections_by_frame[observation.frame_index]
                ),
                default=0.0,
            )
            if maximum_iou >= iou_threshold:
                confirmations.append((observation.frame_index, maximum_iou))
        confirmed_frames = [frame for frame, _ in confirmations]
        maximum_streak = 0
        current_streak = 0
        previous_frame: int | None = None
        for frame in confirmed_frames:
            current_streak = current_streak + 1 if previous_frame == frame - 1 else 1
            maximum_streak = max(maximum_streak, current_streak)
            previous_frame = frame
        rows.append(
            {
                "track_id": track_id,
                "observation_count": len(observations),
                "confirmed_observation_count": len(confirmations),
                "confirmation_ratio": round(len(confirmations) / len(observations), 6),
                "first_confirmed_frame": confirmed_frames[0] if confirmed_frames else None,
                "last_confirmed_frame": confirmed_frames[-1] if confirmed_frames else None,
                "maximum_consecutive_confirmations": maximum_streak,
                "maximum_iou": round(max((value for _, value in confirmations), default=0.0), 6),
            }
        )
    return {
        "schema_version": "deepstream_track_confirmation.v1",
        "track_label": track_label,
        "detection_label": detection_label,
        "iou_threshold": iou_threshold,
        "tracks": rows,
    }


def main() -> int:
    args = parse_args()
    report = build_confirmation_report(
        parse_track_directory(args.tracks),
        parse_detection_directory(args.detections),
        track_label=args.track_label,
        detection_label=args.detection_label,
        iou_threshold=args.iou,
    )
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
