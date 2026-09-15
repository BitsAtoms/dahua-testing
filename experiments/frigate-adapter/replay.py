#!/usr/bin/env python3
"""Replay recorded Frigate MQTT events through the common adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from frigate_adapter import FrigateEventAdapter


def _frame_size(value: str) -> tuple[str, tuple[int, int]]:
    try:
        camera, dimensions = value.split("=", 1)
        width_text, height_text = dimensions.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("expected CAMERA=WIDTHxHEIGHT") from error
    if not camera or width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("expected CAMERA=WIDTHxHEIGHT")
    return camera, (width, height)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording", type=Path)
    parser.add_argument("--instance-id", default="frigate")
    parser.add_argument(
        "--frame-size",
        action="append",
        type=_frame_size,
        required=True,
        help="detect stream dimensions as CAMERA=WIDTHxHEIGHT",
    )
    args = parser.parse_args()

    adapter = FrigateEventAdapter(dict(args.frame_size), args.instance_id)
    with args.recording.open(encoding="utf-8") as recording:
        for line_number, line in enumerate(recording, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                update = adapter.adapt(payload)
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"invalid event on line {line_number}: {error}") from error
            if update is not None:
                print(json.dumps(update, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
