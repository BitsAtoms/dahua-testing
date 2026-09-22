#!/usr/bin/env python3
"""Benchmark Norfair over precomputed spatial-consensus detections."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from benchmark_norfair import CONFIGURATIONS, _new_tracker, _norfair_detections, _percentile
from visual_reid.detector_benchmark import Detection


def benchmark_sequence(frames: list[dict]) -> list[dict]:
    trackers = {config.name: _new_tracker(config) for config in CONFIGURATIONS}
    stats = {
        config.name: {
            "live_ids": set(),
            "frames_with_live_track": 0,
            "frames_with_multiple_live_tracks": 0,
            "latency_ms": [],
        }
        for config in CONFIGURATIONS
    }
    detection_frames = multi_detection_frames = 0
    for frame in frames:
        detections = [
            Detection(tuple(item["box"]), float(item["score"]))
            for item in frame["detections"]
        ]
        detection_frames += bool(detections)
        multi_detection_frames += len(detections) > 1
        for config in CONFIGURATIONS:
            started = time.perf_counter()
            objects = trackers[config.name].update(
                detections=_norfair_detections(detections) if detections else None
            )
            stats[config.name]["latency_ms"].append((time.perf_counter() - started) * 1000)
            live_ids = {
                int(item.global_id)
                for item in objects
                if bool(np.any(item.live_points))
            }
            stats[config.name]["live_ids"].update(live_ids)
            stats[config.name]["frames_with_live_track"] += bool(live_ids)
            stats[config.name]["frames_with_multiple_live_tracks"] += len(live_ids) > 1

    sampled = len(frames)
    results = []
    for config in CONFIGURATIONS:
        item = stats[config.name]
        results.append(
            {
                "configuration": config.name,
                "distance_threshold": config.distance_threshold,
                "hit_counter_max": config.hit_counter_max,
                "initialization_delay": config.initialization_delay,
                "sampled_frames": sampled,
                "detector_frames": detection_frames,
                "detector_frame_rate": round(detection_frames / sampled, 6) if sampled else 0,
                "detector_multi_frames": multi_detection_frames,
                "live_track_count": len(item["live_ids"]),
                "frames_with_live_track": item["frames_with_live_track"],
                "live_track_frame_rate": round(item["frames_with_live_track"] / sampled, 6)
                if sampled
                else 0,
                "frames_with_multiple_live_tracks": item["frames_with_multiple_live_tracks"],
                "tracker_latency_ms_p50": _percentile(sorted(item["latency_ms"]), 0.5),
                "tracker_latency_ms_p95": _percentile(sorted(item["latency_ms"]), 0.95),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.detections.read_text(encoding="utf-8"))
    results = []
    for sequence in source["sequences"]:
        metadata = {
            key: sequence[key]
            for key in ("camera_id", "scenario", "expected_person", "path")
        }
        for metrics in benchmark_sequence(sequence["frames"]):
            results.append({**metadata, **metrics})
    report = {
        "schema_version": "norfair_consensus_benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detections": str(args.detections),
        "sample_fps": source["sample_fps"],
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
