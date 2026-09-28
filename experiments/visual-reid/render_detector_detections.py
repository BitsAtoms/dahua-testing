#!/usr/bin/env python3
"""Render one comparable frame with every configured detector's person boxes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from visual_reid.detector_benchmark import DETECTORS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument(
        "--models",
        type=Path,
        default=Path("experiments/visual-reid/detector-models.example.json"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--second", type=float, default=15.0)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {args.video}")
    capture.set(cv2.CAP_PROP_POS_MSEC, args.second * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"cannot read second {args.second}: {args.video}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    outputs = []
    for config in models:
        model_path = (args.models.parent / config["path"]).resolve()
        detector = DETECTORS[config["backend"]](model_path, config.get("device", "CPU"))
        detections = detector.detect(frame, args.threshold)
        rendered = frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = (round(value) for value in detection.box)
            cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 255, 0), 3)
            cv2.putText(
                rendered,
                f"person {detection.score:.2f}",
                (x1, max(24, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
        output = args.output_dir / f"{config['name']}.jpg"
        cv2.imwrite(str(output), rendered)
        outputs.append({"model": config["name"], "detections": len(detections), "path": str(output)})
    print(json.dumps(outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
