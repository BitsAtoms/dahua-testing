#!/usr/bin/env python3
"""Run configured detectors over one immutable capture manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from visual_reid.detector_benchmark import DETECTORS, benchmark_video


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path("experiments/visual-reid/detector-models.example.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--model", action="append", help="run only the named model; repeatable")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    if args.model:
        requested = set(args.model)
        models = [model for model in models if model["name"] in requested]
        missing = requested - {model["name"] for model in models}
        if missing:
            raise RuntimeError(f"unknown models: {', '.join(sorted(missing))}")
    results = []
    for config in models:
        path = (args.models.parent / config["path"]).resolve()
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash.lower() != config["sha256"].lower():
            raise RuntimeError(f"hash mismatch for {path.name}")
        if config.get("companion_path"):
            companion = (args.models.parent / config["companion_path"]).resolve()
            companion_hash = hashlib.sha256(companion.read_bytes()).hexdigest()
            if companion_hash.lower() != config["companion_sha256"].lower():
                raise RuntimeError(f"hash mismatch for {companion.name}")
        detector = DETECTORS[config["backend"]](path, config.get("device", "CPU"))
        for threshold in config["thresholds"]:
            for video in dataset["videos"]:
                video_path = (args.dataset.parent / video["path"]).resolve()
                metrics = benchmark_video(detector, video_path, threshold=float(threshold), sample_fps=args.sample_fps)
                expected = video["expected_person"]
                metrics["error_rate"] = (
                    metrics["person_frame_rate"] if expected == "absent" else round(1 - metrics["person_frame_rate"], 6)
                ) if expected in {"present", "absent"} else None
                results.append({"model": config["name"], "threshold": threshold, "camera_id": video["camera_id"], "scenario": video["scenario"], "expected_person": expected, **metrics})
    report = {"schema_version": "detector_benchmark_report.v1", "generated_at": datetime.now(timezone.utc).isoformat(), "dataset": str(args.dataset), "sample_fps": args.sample_fps, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
