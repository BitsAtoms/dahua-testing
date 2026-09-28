#!/usr/bin/env python3
"""Replay immutable local recordings with strict ORT provider evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time

import cv2

from visual_reid.onnx_person_detector import OnnxRuntime, YoloXPersonDetector
from visual_reid.rfdetr_person_detector import RfDetrMediumPersonDetector


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("yolox_tiny", "yolox_m", "rfdetr_medium"), required=True)
    parser.add_argument("--provider", choices=("cpu", "directml"), default="cpu")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.threshold < 1 or args.sample_fps <= 0:
        parser.error("threshold must be in (0,1) and sample-fps positive")
    models = json.loads((ROOT / "onnx-detector-models.json").read_text(encoding="utf-8"))["models"]
    model = next(item for item in models if item["name"] == args.model)
    model_path = ROOT / model["path"]
    if model_path.stat().st_size != model["bytes"] or sha256(model_path) != model["sha256"]:
        raise RuntimeError(f"model size or checksum mismatch: {model_path}")
    matrix = json.loads((ROOT / "onnx-detector-matrix.json").read_text(encoding="utf-8"))["recordings"]
    records = []
    summaries = []
    profiles = []
    for item in matrix:
        # A fresh session per clip keeps ORT's finite profiling event buffer
        # below its cap and verifies the requested provider for every replay.
        runtime = OnnxRuntime(model_path, provider=args.provider, device_index=args.device_index)
        detector = (RfDetrMediumPersonDetector(runtime) if args.model == "rfdetr_medium"
                    else YoloXPersonDetector(runtime))
        path = ROOT / item["path"]
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise RuntimeError(f"cannot open recording: {path}")
        source_fps = capture.get(cv2.CAP_PROP_FPS)
        if source_fps <= 0:
            raise RuntimeError(f"missing source FPS: {path}")
        every = max(1, round(source_fps / args.sample_fps))
        recording_hash = sha256(path)
        count = hit = multi = 0
        latencies = []
        frame_index = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % every == 0:
                    start = time.perf_counter()
                    detections = detector.detect(frame, args.threshold)
                    latency = (time.perf_counter() - start) * 1000
                    latencies.append(latency)
                    count += 1
                    hit += bool(detections)
                    multi += len(detections) >= 2
                    records.append({"kind": "frame", "model": args.model,
                                    "scenario": item["scenario"], "camera_id": item["camera_id"],
                                    "recording_sha256": recording_hash, "frame_index": frame_index,
                                    "time_seconds": round(frame_index / source_fps, 3),
                                    "expected_person": item["expected_person"],
                                    "has_detection": bool(detections),
                                    "negative_scene_false_positive": bool(detections) if item["expected_person"] == "absent" else None,
                                    "detections": [{"box_xyxy": [round(float(v), 2) for v in det.box],
                                                    "score": round(det.score, 6)} for det in detections],
                                    "latency_ms": round(latency, 3)})
                frame_index += 1
        finally:
            capture.release()
        if count == 0:
            raise RuntimeError(f"no sampled frames: {path}")
        ordered = sorted(latencies)
        summaries.append({"kind": "scenario_summary", "model": args.model,
                          "scenario": item["scenario"], "camera_id": item["camera_id"],
                          "expected_person": item["expected_person"],
                          "recording_sha256": recording_hash, "recording_bytes": path.stat().st_size,
                          "source_fps": source_fps, "sampled_frames": count,
                          "frames_with_detection": hit, "frame_coverage": round(hit / count, 6),
                          "frames_with_multiple_detections": multi,
                          "negative_false_positive_frames": hit if item["expected_person"] == "absent" else None,
                          "latency_ms_p50": round(statistics.median(latencies), 3),
                          "latency_ms_p95": round(ordered[min(count - 1, int(count * .95))], 3)})
        profiles.append({"scenario": item["scenario"], **runtime.close()})
        print(json.dumps(summaries[-1], sort_keys=True), flush=True)
    profile = {"per_scenario": profiles,
               "node_providers": sorted({node for item in profiles for node in item["node_providers"]}),
               "cpu_fallback": False}  # Every runtime.close() rejects a GPU CPU fallback.
    header = {"kind": "run", "schema_version": "onnx_person_benchmark.v1",
              "generated_at": datetime.now(timezone.utc).isoformat(), "model": args.model,
              "model_sha256": model["sha256"], "provider": args.provider,
              "provider_device_index": args.device_index, "profile": profile,
              "threshold": args.threshold, "sample_fps": args.sample_fps,
              "ort_version": runtime.ort.__version__, "opencv_version": cv2.__version__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps(item, sort_keys=True) for item in
                                      [header, *summaries, *records]) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
