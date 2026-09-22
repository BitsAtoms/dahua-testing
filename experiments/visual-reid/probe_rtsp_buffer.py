#!/usr/bin/env python3
"""Smoke-test one bounded RTSP buffer using a URL supplied via environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import threading

from visual_reid.rtsp_buffer import RollingJpegBuffer, RtspBufferWorker
from visual_reid.configuration import merged_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--url-env", default="VISUAL_REID_RTSP_URL")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--buffer-seconds", type=float, default=10.0)
    parser.add_argument("--max-mib", type=int, default=128)
    args = parser.parse_args()
    url = merged_config(args.env_file).get(args.url_env, "").strip()
    if not url:
        parser.error(f"environment variable {args.url_env} is required")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_args: stop.set())
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    buffer = RollingJpegBuffer(
        max_seconds=args.buffer_seconds,
        max_frames=max(1, round(args.buffer_seconds * args.fps * 2)),
        max_bytes=args.max_mib * 1024 * 1024,
    )
    worker = RtspBufferWorker(args.camera_id, url, buffer, target_fps=args.fps)
    worker.start()
    print(f"rtsp_buffer_started camera={args.camera_id}", flush=True)
    try:
        while not stop.wait(1.0):
            print(json.dumps(worker.status(), ensure_ascii=False), flush=True)
    finally:
        worker.stop()
        print(
            "rtsp_buffer_stopped "
            + json.dumps(worker.status(), ensure_ascii=False),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
