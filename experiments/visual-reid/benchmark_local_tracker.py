"""Run a complete detector plus local-tracker pipeline over one immutable video."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import time

import cv2

from visual_reid.detector_benchmark import OpenVinoYoloXDetector
from visual_reid.deep_sort_realtime_provider import (
    DeepSortRealtimeConfig,
    DeepSortRealtimeProvider,
)
from visual_reid.local_tracking import (
    DetectionFrame,
    FrameDetection,
    LocalTrackUpdateKind,
    PixelBox,
)
from visual_reid.roboflow_botsort import (
    RoboflowBoTSORTConfig,
    RoboflowBoTSORTProvider,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera-id", default="offline_camera")
    parser.add_argument(
        "--provider",
        choices=("roboflow-botsort", "deepsort-openvino"),
        default="roboflow-botsort",
    )
    parser.add_argument("--sample-fps", type=float, default=10.0)
    parser.add_argument("--detector-threshold", type=float, default=0.1)
    parser.add_argument("--activation-threshold", type=float, default=0.6)
    parser.add_argument("--high-confidence-threshold", type=float, default=0.5)
    parser.add_argument("--lost-track-buffer", type=int, default=30)
    parser.add_argument("--minimum-consecutive-frames", type=int, default=2)
    parser.add_argument(
        "--reid-model",
        type=Path,
        default=Path(__file__).parent
        / "models/person-reidentification-retail-0287/FP16"
        / "person-reidentification-retail-0287.xml",
    )
    parser.add_argument("--max-cosine-distance", type=float, default=0.3)
    parser.add_argument("--max-iou-distance", type=float, default=0.7)
    parser.add_argument(
        "--full-state-gating",
        action="store_true",
        help="Use x/y/aspect/height Kalman gating instead of position-only gating.",
    )
    parser.add_argument("--annotated-video", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_fps <= 0:
        raise SystemExit("--sample-fps must be positive")
    detector = OpenVinoYoloXDetector(args.model)
    provider = _provider(args)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"cannot open video: {args.video}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or args.sample_fps
    every = max(1, round(source_fps / args.sample_fps))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = _video_writer(args.annotated_video, args.sample_fps, width, height)

    histories: dict[str, list[dict[str, object]]] = defaultdict(list)
    detection_distribution: Counter[int] = Counter()
    detector_latencies: list[float] = []
    tracker_latencies: list[float] = []
    source_index = sampled_index = 0
    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if source_index % every:
                source_index += 1
                continue

            started = time.perf_counter()
            raw = detector.detect(image, args.detector_threshold)
            detector_latencies.append((time.perf_counter() - started) * 1000)
            detection_distribution[len(raw)] += 1
            detections = tuple(
                detection
                for item in raw
                if (detection := _to_frame_detection(item, width, height)) is not None
            )
            observed_us = round((sampled_index + 1) * 1_000_000 / args.sample_fps)
            frame = DetectionFrame(
                camera_id=args.camera_id,
                frame_index=sampled_index,
                observed_us=observed_us,
                width=width,
                height=height,
                detections=detections,
                image=image,
            )
            started = time.perf_counter()
            updates = provider.process(frame)
            tracker_latencies.append((time.perf_counter() - started) * 1000)
            for update in updates:
                if update.kind is not LocalTrackUpdateKind.OBSERVED:
                    continue
                histories[update.local_track_id].append(
                    {
                        "frame_index": sampled_index,
                        "observed_us": observed_us,
                        "box": [update.box.x1, update.box.y1, update.box.x2, update.box.y2],
                        "confidence": update.confidence,
                    }
                )
                if writer is not None:
                    _draw_update(image, update)
            if writer is not None:
                writer.write(image)
            sampled_index += 1
            source_index += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if sampled_index:
        provider.reset(
            args.camera_id,
            frame_index=sampled_index,
            observed_us=round((sampled_index + 1) * 1_000_000 / args.sample_fps),
        )
    report = {
        "schema_version": "local_tracker_benchmark.v1",
        "video": args.video.name,
        "provider": provider.provider_id,
        "config": {
            "sample_fps": args.sample_fps,
            "detector": "openvino_yolox_tiny",
            "detector_threshold": args.detector_threshold,
            "activation_threshold": args.activation_threshold,
            "high_confidence_threshold": args.high_confidence_threshold,
            "lost_track_buffer": args.lost_track_buffer,
            "minimum_consecutive_frames": args.minimum_consecutive_frames,
            "max_cosine_distance": args.max_cosine_distance,
            "max_iou_distance": args.max_iou_distance,
            "position_only_gating": not args.full_state_gating,
        },
        "sampled_frames": sampled_index,
        "detection_distribution": dict(sorted(detection_distribution.items())),
        "tracks": [
            {
                "local_track_id": track_id,
                "observation_count": len(observations),
                "first_frame": observations[0]["frame_index"],
                "last_frame": observations[-1]["frame_index"],
                "observations": observations,
            }
            for track_id, observations in sorted(histories.items())
        ],
        "track_count": len(histories),
        "latency_ms": {
            "detector_p50": _median(detector_latencies),
            "tracker_p50": _median(tracker_latencies),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"frames={sampled_index} tracks={len(histories)} "
        f"detector_p50_ms={report['latency_ms']['detector_p50']} "
        f"tracker_p50_ms={report['latency_ms']['tracker_p50']}"
    )
    return 0


def _video_writer(path: Path | None, fps: float, width: int, height: int):
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height)
    )
    if not writer.isOpened():
        raise SystemExit(f"cannot create annotated video: {path}")
    return writer


def _provider(args):
    if args.provider == "deepsort-openvino":
        if not args.reid_model.is_file():
            raise SystemExit(f"ReID model not found: {args.reid_model}")
        return DeepSortRealtimeProvider(
            DeepSortRealtimeConfig(
                reid_model=args.reid_model,
                max_age=args.lost_track_buffer,
                n_init=args.minimum_consecutive_frames,
                max_cosine_distance=args.max_cosine_distance,
                max_iou_distance=args.max_iou_distance,
                gating_only_position=not args.full_state_gating,
            )
        )
    return RoboflowBoTSORTProvider(
        RoboflowBoTSORTConfig(
            frame_rate=args.sample_fps,
            lost_track_buffer=args.lost_track_buffer,
            track_activation_threshold=args.activation_threshold,
            minimum_consecutive_frames=args.minimum_consecutive_frames,
            high_conf_det_threshold=args.high_confidence_threshold,
            enable_cmc=False,
        )
    )


def _draw_update(image, update) -> None:
    box = update.box
    x1, y1, x2, y2 = (round(box.x1), round(box.y1), round(box.x2), round(box.y2))
    cv2.rectangle(image, (x1, y1), (x2, y2), (50, 220, 50), 2)
    cv2.putText(
        image,
        update.local_track_id,
        (x1, max(20, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (50, 220, 50),
        2,
        cv2.LINE_AA,
    )


def _to_frame_detection(item, width: int, height: int) -> FrameDetection | None:
    x1, y1, x2, y2 = item.box
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(width), x2), min(float(height), y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return FrameDetection(PixelBox(x1, y1, x2, y2), item.score)


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
