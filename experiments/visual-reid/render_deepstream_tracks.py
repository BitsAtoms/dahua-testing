#!/usr/bin/env python3
"""Render DeepStream KITTI track output over its source recording."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import cv2

from analyze_deepstream_tracks import TrackObservation, parse_track_directory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="Person")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    by_frame: dict[int, list[TrackObservation]] = defaultdict(list)
    for item in parse_track_directory(args.tracks):
        if item.label == args.label:
            by_frame[item.frame_index].append(item)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"cannot open video: {args.video}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(args.output), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height)
    )
    if not writer.isOpened():
        raise SystemExit(f"cannot create video: {args.output}")

    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            for item in by_frame.get(frame_index, ()):
                left, top, right, bottom = (round(value) for value in item.box)
                color = track_color(item.track_id)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 3)
                cv2.putText(
                    frame,
                    f"{item.label} {item.track_id}",
                    (left, max(25, top - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    color,
                    2,
                    cv2.LINE_AA,
                )
            writer.write(frame)
            frame_index += 1
    finally:
        capture.release()
        writer.release()
    print(f"frames={frame_index} output={args.output}")
    return 0


def track_color(track_id: int) -> tuple[int, int, int]:
    return (
        64 + (track_id * 67) % 192,
        64 + (track_id * 113) % 192,
        64 + (track_id * 151) % 192,
    )


if __name__ == "__main__":
    raise SystemExit(main())
