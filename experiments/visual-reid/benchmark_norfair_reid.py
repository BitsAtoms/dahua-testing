#!/usr/bin/env python3
"""Evaluate Norfair with spatial and body-ReID evidence on recorded detections."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
import openvino as ov
from norfair import Detection as NorfairDetection
from norfair import Tracker

from visual_reid.detector_benchmark import _iou


@dataclass(frozen=True)
class AppearanceConfig:
    name: str
    appearance_weight: float
    appearance_gate: float
    distance_threshold: float
    reid_threshold: float


CONFIGURATIONS = (
    AppearanceConfig("appearance-0.50_gate-0.40", 0.50, 0.40, 0.60, 0.40),
    AppearanceConfig("appearance-0.65_gate-0.35", 0.65, 0.35, 0.55, 0.35),
    AppearanceConfig("appearance-0.70_gate-0.30", 0.70, 0.30, 0.50, 0.30),
)


class BodyEmbedder:
    def __init__(self, model_path: Path) -> None:
        core = ov.Core()
        compiled = core.compile_model(core.read_model(model_path), "CPU")
        self.compiled = compiled
        self.output = compiled.output(0)

    def embed(self, frame: np.ndarray, box: tuple[float, float, float, float]):
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = box
        pad_x = (x2 - x1) * 0.03
        pad_y = (y2 - y1) * 0.03
        left = max(0, round(x1 - pad_x))
        top = max(0, round(y1 - pad_y))
        right = min(width, round(x2 + pad_x))
        bottom = min(height, round(y2 + pad_y))
        crop = frame[top:bottom, left:right]
        if crop.size == 0 or crop.shape[1] < 32 or crop.shape[0] < 64:
            return None
        resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR)
        tensor = resized.transpose(2, 0, 1)[None].astype(np.float32)
        vector = self.compiled([tensor])[self.output].reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else None


def _verified_model(experiment_root: Path) -> Path:
    manifest = json.loads((experiment_root / "model-manifest.json").read_text(encoding="utf-8"))
    model = next(item for item in manifest["models"] if item["name"] == "person-reidentification-retail-0287")
    for artifact in model["files"]:
        path = experiment_root / "models" / artifact["path"]
        if hashlib.sha384(path.read_bytes()).hexdigest().lower() != artifact["sha384"].lower():
            raise RuntimeError(f"hash mismatch for {path.name}")
    return experiment_root / "models" / model["files"][0]["path"]


def _load_sequence_embeddings(sequence: dict, video_path: Path, embedder: BodyEmbedder):
    frames_by_source = {item["source_frame_index"]: item for item in sequence["frames"]}
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    processed = []
    source_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            item = frames_by_source.get(source_index)
            if item is not None:
                detections = []
                for index, raw in enumerate(item["detections"]):
                    box = tuple(float(value) for value in raw["box"])
                    detections.append(
                        {
                            "detection_index": index,
                            "box": box,
                            "score": float(raw["score"]),
                            "embedding": embedder.embed(frame, box),
                        }
                    )
                processed.append({**item, "detections": detections})
            source_index += 1
    finally:
        capture.release()
    if len(processed) != len(sequence["frames"]):
        raise RuntimeError("video ended before all sampled detection frames")
    return processed


def _appearance_distance(left, right) -> float | None:
    if left is None or right is None:
        return None
    return max(0.0, min(2.0, 1.0 - float(np.dot(left, right))))


def _tracker(config: AppearanceConfig) -> Tracker:
    def distance(detection, tracked_object) -> float:
        spatial = 1.0 - _iou(
            tuple(float(value) for value in detection.points.reshape(-1)),
            tuple(float(value) for value in tracked_object.estimate.reshape(-1)),
        )
        appearance = _appearance_distance(
            detection.embedding,
            tracked_object.last_detection.embedding,
        )
        if appearance is None:
            return spatial
        if appearance > config.appearance_gate:
            return 1.0
        return config.appearance_weight * appearance + (1 - config.appearance_weight) * spatial

    def reid_distance(initializing_object, unmatched_object) -> float:
        distance_value = _appearance_distance(
            initializing_object.last_detection.embedding,
            unmatched_object.last_detection.embedding,
        )
        return distance_value if distance_value is not None else 1.0

    return Tracker(
        distance_function=distance,
        distance_threshold=config.distance_threshold,
        hit_counter_max=15,
        initialization_delay=2,
        past_detections_length=10,
        reid_distance_function=reid_distance,
        reid_distance_threshold=config.reid_threshold,
        reid_hit_counter_max=30,
    )


def _identity_prototypes(frames: list[dict]):
    for frame in frames:
        usable = [item["embedding"] for item in frame["detections"] if item["embedding"] is not None]
        if len(usable) >= 2:
            return usable[0], usable[1]
    raise RuntimeError("no frame contains two usable body embeddings")


def _identity_labels(embeddings: list[np.ndarray], prototypes) -> list[str]:
    if len(embeddings) == 2:
        direct = float(np.dot(embeddings[0], prototypes[0]) + np.dot(embeddings[1], prototypes[1]))
        swapped = float(np.dot(embeddings[0], prototypes[1]) + np.dot(embeddings[1], prototypes[0]))
        return ["a", "b"] if direct >= swapped else ["b", "a"]
    return ["a" if float(np.dot(item, prototypes[0])) >= float(np.dot(item, prototypes[1])) else "b" for item in embeddings]


def benchmark(frames: list[dict], config: AppearanceConfig) -> dict:
    tracker = _tracker(config)
    prototypes = _identity_prototypes(frames)
    identity_by_detection = {}
    for frame in frames:
        usable = [item for item in frame["detections"] if item["embedding"] is not None]
        labels = _identity_labels([item["embedding"] for item in usable], prototypes)
        for item, label in zip(usable, labels):
            identity_by_detection[(frame["sampled_index"], item["detection_index"])] = label

    track_identity_counts = defaultdict(Counter)
    live_ids = set()
    frames_with_live = frames_with_multiple = 0
    latencies = []
    for frame in frames:
        detections = [
            NorfairDetection(
                points=np.asarray(((item["box"][0], item["box"][1]), (item["box"][2], item["box"][3]))),
                scores=np.asarray((item["score"], item["score"])),
                label="person",
                embedding=item["embedding"],
                data={"sampled_index": frame["sampled_index"], "detection_index": item["detection_index"]},
            )
            for item in frame["detections"]
        ]
        started = time.perf_counter()
        objects = tracker.update(detections=detections or None)
        latencies.append((time.perf_counter() - started) * 1000)
        current_ids = set()
        for item in objects:
            data = item.last_detection.data
            if data.get("sampled_index") != frame["sampled_index"]:
                continue
            track_id = int(item.global_id)
            current_ids.add(track_id)
            live_ids.add(track_id)
            label = identity_by_detection.get((data["sampled_index"], data["detection_index"]))
            if label:
                track_identity_counts[track_id][label] += 1
        frames_with_live += bool(current_ids)
        frames_with_multiple += len(current_ids) > 1

    identity_tracks = defaultdict(set)
    mixed_tracks = []
    details = {}
    for track_id, counts in sorted(track_identity_counts.items()):
        dominant, dominant_count = counts.most_common(1)[0]
        total = sum(counts.values())
        identity_tracks[dominant].add(track_id)
        purity = dominant_count / total
        if purity < 0.8:
            mixed_tracks.append(track_id)
        details[str(track_id)] = {"a": counts["a"], "b": counts["b"], "purity": round(purity, 6)}
    ordered_latency = sorted(latencies)
    return {
        "configuration": config.name,
        "appearance_weight": config.appearance_weight,
        "appearance_gate": config.appearance_gate,
        "distance_threshold": config.distance_threshold,
        "reid_threshold": config.reid_threshold,
        "sampled_frames": len(frames),
        "usable_embedding_detections": len(identity_by_detection),
        "live_track_count": len(live_ids),
        "identity_a_track_count": len(identity_tracks["a"]),
        "identity_b_track_count": len(identity_tracks["b"]),
        "mixed_track_count": len(mixed_tracks),
        "mixed_track_ids": mixed_tracks,
        "frames_with_live_track": frames_with_live,
        "frames_with_multiple_live_tracks": frames_with_multiple,
        "tracker_latency_ms_p50": _percentile(ordered_latency, 0.5),
        "tracker_latency_ms_p95": _percentile(ordered_latency, 0.95),
        "track_identity_counts": details,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return round(values[min(len(values) - 1, round((len(values) - 1) * fraction))], 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.detections.read_text(encoding="utf-8"))
    experiment_root = Path(__file__).resolve().parent
    embedder = BodyEmbedder(_verified_model(experiment_root))
    results = []
    for sequence in source["sequences"]:
        dataset_path = Path(source["dataset"])
        video_path = (dataset_path.parent / sequence["path"]).resolve()
        frames = _load_sequence_embeddings(sequence, video_path, embedder)
        for config in CONFIGURATIONS:
            results.append(
                {
                    "camera_id": sequence["camera_id"],
                    "scenario": sequence["scenario"],
                    **benchmark(frames, config),
                }
            )
    report = {
        "schema_version": "norfair_reid_benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detections": str(args.detections),
        "model": "person-reidentification-retail-0287",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
