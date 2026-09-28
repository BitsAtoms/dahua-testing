#!/usr/bin/env python3
"""Benchmark spatial agreement between the active SSD and YOLOX-Tiny."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from visual_reid.detector_benchmark import (
    ConsensusDetector,
    OpenVinoYoloXDetector,
    TfliteSsdDetector,
    benchmark_video,
)


def _verified_path(models_path: Path, config: dict[str, object]) -> Path:
    path = (models_path.parent / str(config["path"])).resolve()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != str(config["sha256"]).lower():
        raise RuntimeError(f"hash mismatch for {path.name}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--models",
        type=Path,
        default=Path("experiments/visual-reid/detector-models.example.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--guard-threshold", type=float, default=0.5)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument(
        "--candidate-threshold",
        type=float,
        action="append",
        default=None,
        help="YOLOX threshold; repeatable (defaults: 0.4, 0.5, 0.55, 0.6)",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    configs = {
        item["name"]: item
        for item in json.loads(args.models.read_text(encoding="utf-8"))["models"]
    }
    guard = TfliteSsdDetector(_verified_path(args.models, configs["frigate_cpu_ssd"]))
    candidate = OpenVinoYoloXDetector(_verified_path(args.models, configs["yolox_tiny"]))
    detector = ConsensusDetector(
        guard,
        candidate,
        guard_threshold=args.guard_threshold,
        iou_threshold=args.iou_threshold,
    )
    thresholds = args.candidate_threshold or [0.4, 0.5, 0.55, 0.6]
    results = []
    for threshold in thresholds:
        for video in dataset["videos"]:
            video_path = (args.dataset.parent / video["path"]).resolve()
            metrics = benchmark_video(
                detector,
                video_path,
                threshold=threshold,
                sample_fps=args.sample_fps,
            )
            expected = video["expected_person"]
            metrics["error_rate"] = (
                metrics["person_frame_rate"]
                if expected == "absent"
                else round(1 - metrics["person_frame_rate"], 6)
            )
            results.append(
                {
                    "model": "frigate_cpu_ssd+yolox_tiny_consensus",
                    "guard_threshold": args.guard_threshold,
                    "candidate_threshold": threshold,
                    "iou_threshold": args.iou_threshold,
                    "camera_id": video["camera_id"],
                    "scenario": video["scenario"],
                    "expected_person": expected,
                    **metrics,
                }
            )
    report = {
        "schema_version": "detector_consensus_benchmark_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "sample_fps": args.sample_fps,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
