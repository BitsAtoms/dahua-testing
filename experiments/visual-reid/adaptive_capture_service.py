#!/usr/bin/env python3
"""Tail normalized tracks and adapt bounded RTSP capture rates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import threading
import time

from visual_reid.adaptive_capture import (
    AdaptiveCaptureCoordinator,
    AdaptiveCapturePolicy,
    EvidenceReservoir,
)
from visual_reid.adaptive_audit import AdaptiveAuditStore
from visual_reid.adaptive_media import AdaptiveMediaStore
from visual_reid.adaptive_service import (
    TrackUpdateProcessor,
    latest_rowid,
    load_updates_after,
)
from visual_reid.configuration import merged_config
from visual_reid.face_detection import OpenVinoFaceDetector
from visual_reid.rtsp_buffer import RollingJpegBuffer, RtspBufferWorker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/visual-reid/adaptive-capture.local.json"),
    )
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument(
        "--audit-database",
        type=Path,
        default=Path("runtime/visual-reid/adaptive-capture.sqlite3"),
    )
    parser.add_argument(
        "--adaptive-media-database",
        type=Path,
        default=Path("runtime/visual-reid/adaptive-media.sqlite3"),
    )
    parser.add_argument(
        "--adaptive-media-root",
        type=Path,
        default=Path("runtime/visual-reid/adaptive-media"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--status-seconds", type=float, default=10.0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--face-detector-model",
        type=Path,
        default=Path(
            "experiments/visual-reid/models/face-detection-retail-0004/FP16/"
            "face-detection-retail-0004.xml"
        ),
    )
    parser.add_argument("--face-device", default="CPU")
    parser.add_argument(
        "--face-landmark-model",
        type=Path,
        default=Path(
            "experiments/visual-reid/models/"
            "landmarks-regression-retail-0009/FP16/"
            "landmarks-regression-retail-0009.xml"
        ),
    )
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.batch_size <= 0 or args.status_seconds <= 0:
        parser.error("poll-seconds, batch-size and status-seconds must be positive")
    config = _load_config(args.config)
    secrets = merged_config(args.env_file)
    normal_fps = _positive(config, "normal_fps")
    reinforce_fps = _positive(config, "reinforce_fps")
    active_track_timeout_seconds = _positive_default(
        config, "active_track_timeout_seconds", 15.0
    )
    if reinforce_fps < normal_fps:
        parser.error("reinforce_fps cannot be lower than normal_fps")
    workers: dict[str, RtspBufferWorker] = {}
    buffers: dict[str, RollingJpegBuffer] = {}
    for camera in config["cameras"]:
        if not camera.get("enabled", True):
            continue
        camera_id = str(camera.get("camera_id", "")).strip()
        env_name = str(camera.get("url_env", "")).strip()
        if not camera_id or not env_name:
            parser.error("each enabled camera needs camera_id and url_env")
        url = secrets.get(env_name, "").strip()
        if not url:
            parser.error(f"environment variable {env_name} is required")
        buffer = RollingJpegBuffer(
            max_seconds=_positive(config, "buffer_seconds"),
            max_frames=int(_positive(config, "max_frames")),
            max_bytes=int(_positive(config, "max_mib_per_camera")) * 1024 * 1024,
        )
        buffers[camera_id] = buffer
        workers[camera_id] = RtspBufferWorker(
            camera_id, url, buffer, target_fps=normal_fps
        )
    if not workers:
        parser.error("configuration has no enabled cameras")

    def set_camera_fps(camera_id: str, fps: float) -> None:
        workers[camera_id].set_target_fps(fps)

    coordinator = AdaptiveCaptureCoordinator(
        EvidenceReservoir(max_per_modality=5),
        AdaptiveCapturePolicy(
            normal_fps=normal_fps,
            reinforce_fps=reinforce_fps,
        ),
        set_camera_fps,
    )
    face_detector = OpenVinoFaceDetector(
        args.face_detector_model,
        args.face_device,
        landmark_model_path=args.face_landmark_model,
    )
    processor = TrackUpdateProcessor(coordinator, buffers, face_detector)
    audit = AdaptiveAuditStore(args.audit_database)
    audit.cleanup()
    adaptive_media = AdaptiveMediaStore(
        args.adaptive_media_database, args.adaptive_media_root
    )
    adaptive_media.cleanup()
    adaptive_media.enforce_limits()
    run_id = audit.start_run(
        list(workers),
        normal_fps=normal_fps,
        reinforce_fps=reinforce_fps,
    )
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_args: stop.set())
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    cursor = latest_rowid(args.receiver_database)
    for worker in workers.values():
        worker.start()
    print(
        "adaptive_capture_started "
        f"run={run_id} cameras={','.join(sorted(workers))} cursor={cursor}",
        flush=True,
    )
    next_status = time.monotonic() + args.status_seconds
    synthetic_rowid = -1
    try:
        while not stop.is_set():
            rows = load_updates_after(
                args.receiver_database, cursor, limit=args.batch_size
            )
            for rowid, received_us, update in rows:
                cursor = rowid
                result = processor.process(update, received_us)
                if result is not None:
                    audit.save_decision(
                        run_id,
                        rowid,
                        received_us,
                        str(update.get("message_id", "")),
                        result,
                        actual_camera_fps=float(
                            workers[result.camera_id].status()["target_fps"]
                        ),
                    )
                    if result.phase in {"end", "snapshot"}:
                        adaptive_media.save_observations(
                            coordinator.reservoir.observations(result.track_id)
                        )
                if result is not None and (args.verbose or result.decision.reinforce):
                    print(
                        "adaptive_capture "
                        f"camera={result.camera_id} track={result.track_id} "
                        f"phase={result.phase} state={result.decision.state.value} "
                        f"accepted={result.accepted_observations} "
                        f"fps={result.decision.target_fps:g} "
                        f"reasons={','.join(result.decision.reasons) or '-'}",
                        flush=True,
                    )
            for result in processor.expire_stale(
                time.time_ns() // 1_000,
                timeout_seconds=active_track_timeout_seconds,
            ):
                audit.save_decision(
                    run_id,
                    synthetic_rowid,
                    time.time_ns() // 1_000,
                    f"adaptive-timeout:{run_id}:{result.track_id}",
                    result,
                    actual_camera_fps=float(
                        workers[result.camera_id].status()["target_fps"]
                    ),
                )
                adaptive_media.save_observations(
                    coordinator.reservoir.observations(result.track_id)
                )
                synthetic_rowid -= 1
                print(
                    "adaptive_capture "
                    f"camera={result.camera_id} track={result.track_id} "
                    "phase=timeout state=pending accepted=0 "
                    f"fps={result.decision.target_fps:g} reasons=stale_track",
                    flush=True,
                )
            if not rows:
                stop.wait(args.poll_seconds)
            if time.monotonic() >= next_status:
                print(
                    "adaptive_capture_status "
                    + json.dumps(
                        [worker.status() for worker in workers.values()],
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                next_status = time.monotonic() + args.status_seconds
    except KeyboardInterrupt:
        stop.set()
    finally:
        for worker in workers.values():
            worker.stop()
        audit.finish_run(run_id)
        audit.close()
        adaptive_media.close()
        statuses = [worker.status() for worker in workers.values()]
        print(
            "adaptive_capture_stopped "
            + json.dumps(statuses, ensure_ascii=False),
            flush=True,
        )
    return 0


def _load_config(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("adaptive capture config must be an object")
    if document.get("schema_version") != "adaptive_capture_config.v1":
        raise ValueError("unsupported adaptive capture config schema")
    if not isinstance(document.get("cameras"), list):
        raise ValueError("adaptive capture cameras must be a list")
    return document


def _positive(document: dict, field: str) -> float:
    value = document.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


def _positive_default(document: dict, field: str, default: float) -> float:
    value = document.get(field, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
