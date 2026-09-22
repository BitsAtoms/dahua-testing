#!/usr/bin/env python3
"""Capture synchronized, bounded RTSP videos without logging secret URLs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading

from visual_reid.configuration import merged_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", action="append", required=True, help="camera_id=URL_ENV")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--expected-person", choices=("present", "absent", "mixed"), required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-root", type=Path, default=Path("experiments/visual-reid/output/detector-benchmark"))
    args = parser.parse_args()
    if args.duration <= 0 or args.duration > 600:
        parser.error("duration must be within 0..600 seconds")
    config = merged_config(args.env_file)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session = args.output_root / f"{timestamp}-{args.scenario}"
    session.mkdir(parents=True, exist_ok=False)
    videos, errors = [], []

    def capture(spec: str) -> None:
        camera_id, env_name = spec.split("=", 1)
        url = config.get(env_name, "").strip()
        if not url:
            errors.append(f"missing environment variable: {env_name}")
            return
        target = session / f"{camera_id}.mkv"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url, "-t", str(args.duration), "-an", "-c:v", "copy", "-y", str(target)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            errors.append(f"capture failed for {camera_id}: ffmpeg exit {result.returncode}")
            return
        videos.append({"camera_id": camera_id, "scenario": args.scenario, "expected_person": args.expected_person, "path": target.name})

    threads = [threading.Thread(target=capture, args=(spec,)) for spec in args.camera]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    if errors:
        raise RuntimeError("; ".join(errors))
    manifest = {"schema_version": "detector_benchmark_dataset.v1", "captured_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": args.duration, "videos": sorted(videos, key=lambda item: item["camera_id"])}
    (session / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"session": str(session), "videos": len(videos)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
