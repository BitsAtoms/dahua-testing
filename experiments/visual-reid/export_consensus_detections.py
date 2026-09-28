#!/usr/bin/env python3
"""Export per-frame SSD+YOLOX consensus boxes for tracker benchmarks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import cv2

from visual_reid.detector_benchmark import (
    ConsensusDetector,
    OpenVinoYoloXDetector,
    TfliteSsdDetector,
)


def _verified_path(models_path: Path, config: dict) -> Path:
    path = (models_path.parent / config["path"]).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest().lower() != config["sha256"].lower():
        raise RuntimeError(f"hash mismatch for {path.name}")
    return path


def _sequence(detector, path: Path, threshold: float, sample_fps: float) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or sample_fps
    every = max(1, round(source_fps / sample_fps))
    frame_index = sampled_index = 0
    frames = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % every:
                frame_index += 1
                continue
            detections = detector.detect(frame, threshold)
            frames.append(
                {
                    "sampled_index": sampled_index,
                    "source_frame_index": frame_index,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "detections": [
                        {"box": list(item.box), "score": item.score}
                        for item in detections
                    ],
                }
            )
            sampled_index += 1
            frame_index += 1
    finally:
        capture.release()
    return {"source_fps": source_fps, "sampled_frames": len(frames), "frames": frames}


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
    parser.add_argument("--candidate-threshold", type=float, default=0.4)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    models = {
        item["name"]: item
        for item in json.loads(args.models.read_text(encoding="utf-8"))["models"]
    }
    detector = ConsensusDetector(
        TfliteSsdDetector(_verified_path(args.models, models["frigate_cpu_ssd"])),
        OpenVinoYoloXDetector(_verified_path(args.models, models["yolox_tiny"])),
        guard_threshold=args.guard_threshold,
        iou_threshold=args.iou_threshold,
    )
    sequences = []
    for video in dataset["videos"]:
        path = (args.dataset.parent / video["path"]).resolve()
        sequences.append(
            {
                **video,
                **_sequence(detector, path, args.candidate_threshold, args.sample_fps),
            }
        )
    report = {
        "schema_version": "detector_consensus_sequences.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "sample_fps": args.sample_fps,
        "guard_threshold": args.guard_threshold,
        "candidate_threshold": args.candidate_threshold,
        "iou_threshold": args.iou_threshold,
        "sequences": sequences,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sequences": len(sequences)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
