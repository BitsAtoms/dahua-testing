#!/usr/bin/env python3
"""Benchmark Norfair over the pinned YOLOX detector on controlled captures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from norfair import Detection as NorfairDetection
from norfair import Tracker

from visual_reid.detector_benchmark import OpenVinoYoloXDetector


@dataclass(frozen=True)
class TrackerConfig:
    name: str
    distance_threshold: float
    hit_counter_max: int
    initialization_delay: int


CONFIGURATIONS = (
    TrackerConfig("iou-0.7_hit-10_init-2", 0.7, 10, 2),
    TrackerConfig("iou-0.7_hit-15_init-4", 0.7, 15, 4),
    TrackerConfig("iou-0.8_hit-10_init-2", 0.8, 10, 2),
    TrackerConfig("iou-0.9_hit-15_init-2", 0.9, 15, 2),
)


def _new_tracker(config: TrackerConfig) -> Tracker:
    return Tracker(
        distance_function="iou",
        distance_threshold=config.distance_threshold,
        hit_counter_max=config.hit_counter_max,
        initialization_delay=config.initialization_delay,
    )


def _norfair_detections(detections) -> list[NorfairDetection]:
    return [
        NorfairDetection(
            points=np.asarray(((item.box[0], item.box[1]), (item.box[2], item.box[3]))),
            scores=np.asarray((item.score, item.score)),
            label="person",
            data={"box": item.box, "score": item.score},
        )
        for item in detections
    ]


def benchmark_video(
    detector: OpenVinoYoloXDetector,
    path: Path,
    *,
    threshold: float,
    sample_fps: float,
) -> list[dict]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or sample_fps
    every = max(1, round(source_fps / sample_fps))
    trackers = {config.name: _new_tracker(config) for config in CONFIGURATIONS}
    stats = {
        config.name: {
            "initialized_ids": set(),
            "live_ids": set(),
            "frames_with_initialized_track": 0,
            "frames_with_live_track": 0,
            "frames_with_multiple_live_tracks": 0,
            "tracker_latency_ms": [],
        }
        for config in CONFIGURATIONS
    }
    sampled = detection_frames = multi_detection_frames = frame_index = 0
    detector_latency_ms: list[float] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % every:
                frame_index += 1
                continue
            started = time.perf_counter()
            detections = detector.detect(frame, threshold)
            detector_latency_ms.append((time.perf_counter() - started) * 1000)
            detection_frames += bool(detections)
            multi_detection_frames += len(detections) > 1
            for config in CONFIGURATIONS:
                tracker_started = time.perf_counter()
                objects = trackers[config.name].update(
                    detections=_norfair_detections(detections) if detections else None
                )
                stats[config.name]["tracker_latency_ms"].append(
                    (time.perf_counter() - tracker_started) * 1000
                )
                initialized_ids = {int(item.global_id) for item in objects}
                live_ids = {
                    int(item.global_id)
                    for item in objects
                    if bool(np.any(item.live_points))
                }
                stats[config.name]["initialized_ids"].update(initialized_ids)
                stats[config.name]["live_ids"].update(live_ids)
                stats[config.name]["frames_with_initialized_track"] += bool(initialized_ids)
                stats[config.name]["frames_with_live_track"] += bool(live_ids)
                stats[config.name]["frames_with_multiple_live_tracks"] += len(live_ids) > 1
            sampled += 1
            frame_index += 1
    finally:
        capture.release()

    results = []
    for config in CONFIGURATIONS:
        item = stats[config.name]
        tracker_latencies = sorted(item.pop("tracker_latency_ms"))
        initialized_ids = sorted(item.pop("initialized_ids"))
        live_ids = sorted(item.pop("live_ids"))
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
                "initialized_track_count": len(initialized_ids),
                "live_track_count": len(live_ids),
                "frames_with_initialized_track": item["frames_with_initialized_track"],
                "frames_with_live_track": item["frames_with_live_track"],
                "live_track_frame_rate": round(item["frames_with_live_track"] / sampled, 6)
                if sampled
                else 0,
                "frames_with_multiple_live_tracks": item["frames_with_multiple_live_tracks"],
                "tracker_latency_ms_p50": _percentile(tracker_latencies, 0.5),
                "tracker_latency_ms_p95": _percentile(tracker_latencies, 0.95),
                "detector_latency_ms_p50": _percentile(sorted(detector_latency_ms), 0.5),
            }
        )
    return results


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, round((len(values) - 1) * fraction))
    return round(values[index], 3)


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
    parser.add_argument("--threshold", type=float, default=0.4)
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    model = next(item for item in models if item["name"] == "yolox_tiny")
    model_path = (args.models.parent / model["path"]).resolve()
    if hashlib.sha256(model_path.read_bytes()).hexdigest().lower() != model["sha256"].lower():
        raise RuntimeError(f"hash mismatch for {model_path.name}")
    detector = OpenVinoYoloXDetector(model_path)

    results = []
    for video in dataset["videos"]:
        video_path = (args.dataset.parent / video["path"]).resolve()
        for metrics in benchmark_video(
            detector,
            video_path,
            threshold=args.threshold,
            sample_fps=args.sample_fps,
        ):
            results.append({**video, **metrics})
    report = {
        "schema_version": "norfair_detector_benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "detector": "yolox_tiny",
        "detector_threshold": args.threshold,
        "sample_fps": args.sample_fps,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
