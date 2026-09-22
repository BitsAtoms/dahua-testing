#!/usr/bin/env python3
"""Tail normalized tracks and persist detector-consensus shadow decisions."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import signal
import threading
import time

from visual_reid.adaptive_service import latest_rowid, load_updates_after
from visual_reid.configuration import merged_config
from visual_reid.detector_benchmark import OpenVinoYoloXDetector
from visual_reid.detector_consensus import ConsensusAuditStore, ShadowConsensusValidator
from visual_reid.rtsp_buffer import RollingJpegBuffer, RtspBufferWorker


PINNED_YOLOX_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument(
        "--audit-database",
        type=Path,
        default=Path("runtime/visual-reid/detector-consensus.sqlite3"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("experiments/visual-reid/models/detectors/yolox_tiny.onnx"),
    )
    parser.add_argument(
        "--capture-config",
        type=Path,
        default=Path("experiments/visual-reid/adaptive-capture.local.json"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--device", default="CPU")
    parser.add_argument("--guard-threshold", type=float, default=0.5)
    parser.add_argument("--candidate-threshold", type=float, default=0.4)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--status-seconds", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--alignment-seconds", type=float, default=1.5)
    parser.add_argument("--min-track-interval-seconds", type=float, default=1.0)
    parser.add_argument("--snapshots-only", action="store_true")
    parser.add_argument("--replay-existing", action="store_true")
    parser.add_argument("--start-rowid", type=int)
    parser.add_argument("--max-evaluations", type=int)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.status_seconds <= 0 or args.batch_size <= 0:
        parser.error("poll-seconds, status-seconds and batch-size must be positive")
    if args.alignment_seconds <= 0 or args.min_track_interval_seconds <= 0:
        parser.error("alignment and track interval must be positive")
    if args.max_evaluations is not None and args.max_evaluations <= 0:
        parser.error("max-evaluations must be positive")
    if args.start_rowid is not None and args.start_rowid < 0:
        parser.error("start-rowid cannot be negative")
    if args.replay_existing and args.start_rowid is not None:
        parser.error("replay-existing and start-rowid are mutually exclusive")
    actual_hash = hashlib.sha256(args.model.read_bytes()).hexdigest()
    if actual_hash != PINNED_YOLOX_SHA256:
        raise RuntimeError(f"hash mismatch for {args.model.name}")

    detector = OpenVinoYoloXDetector(args.model, args.device)
    validator = ShadowConsensusValidator(
        detector,
        guard_threshold=args.guard_threshold,
        candidate_threshold=args.candidate_threshold,
        iou_threshold=args.iou_threshold,
    )
    audit = ConsensusAuditStore(args.audit_database)
    removed = audit.cleanup()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_args: stop.set())
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    buffers: dict[str, RollingJpegBuffer] = {}
    workers: dict[str, RtspBufferWorker] = {}
    if not args.snapshots_only:
        capture_config = _load_capture_config(args.capture_config)
        secrets = merged_config(args.env_file)
        target_fps = float(capture_config.get("normal_fps", 1.0))
        for camera in capture_config["cameras"]:
            if not camera.get("enabled", True):
                continue
            camera_id = str(camera.get("camera_id", "")).strip()
            env_name = str(camera.get("url_env", "")).strip()
            url = secrets.get(env_name, "").strip()
            if not camera_id or not env_name or not url:
                raise ValueError(f"missing RTSP configuration for {camera_id or 'camera'}")
            buffer = RollingJpegBuffer(
                max_seconds=float(capture_config.get("buffer_seconds", 10.0)),
                max_frames=int(capture_config.get("max_frames", 100)),
                max_bytes=int(capture_config.get("max_mib_per_camera", 128))
                * 1024
                * 1024,
            )
            buffers[camera_id] = buffer
            workers[camera_id] = RtspBufferWorker(
                camera_id, url, buffer, target_fps=target_fps
            )
        for worker in workers.values():
            worker.start()
        warm_deadline = time.monotonic() + max(2.0, 2.0 / target_fps)
        while (
            workers
            and not all(buffer.stats()["frames"] for buffer in buffers.values())
            and time.monotonic() < warm_deadline
            and not stop.wait(0.1)
        ):
            pass
    while not args.receiver_database.is_file() and not stop.wait(args.poll_seconds):
        pass
    if stop.is_set():
        audit.close()
        return 0
    cursor = (
        args.start_rowid
        if args.start_rowid is not None
        else (0 if args.replay_existing else latest_rowid(args.receiver_database))
    )
    counters = {"evaluated": 0, "not_evaluated": 0}
    last_track_observed_us: dict[str, int] = {}
    active_updates: dict[str, dict] = {}
    last_stationary_poll: dict[str, float] = {}
    last_stationary_frame: dict[str, int] = {}
    print(
        "detector_consensus_shadow_started "
        f"cursor={cursor} guard={args.guard_threshold:g} "
        f"candidate={args.candidate_threshold:g} iou={args.iou_threshold:g} "
        f"retention_removed={removed} live_frames={bool(workers)}",
        flush=True,
    )
    next_status = time.monotonic() + args.status_seconds
    try:
        while not stop.is_set():
            rows = load_updates_after(args.receiver_database, cursor, limit=args.batch_size)
            for rowid, received_us, update in rows:
                cursor = rowid
                phase = str(update.get("phase", ""))
                track_id = str(update.get("track_id", ""))
                observed_us = _update_observed_us(update, received_us)
                if phase in {"new", "update"} and track_id:
                    active_updates[track_id] = update
                elif phase == "end":
                    active_updates.pop(track_id, None)
                    last_stationary_poll.pop(track_id, None)
                    last_stationary_frame.pop(track_id, None)
                if phase in {"new", "update"} and track_id in last_track_observed_us:
                    interval_us = round(args.min_track_interval_seconds * 1_000_000)
                    if observed_us - last_track_observed_us[track_id] < interval_us:
                        continue
                if phase in {"new", "update"} and update.get("camera_id") in buffers:
                    evaluation = _evaluate_live(
                        validator,
                        update,
                        observed_us,
                        buffers[str(update["camera_id"])],
                        args.alignment_seconds,
                    )
                    last_track_observed_us[track_id] = observed_us
                else:
                    evaluation = validator.evaluate(update)
                if evaluation is None:
                    continue
                _record_evaluation(audit, evaluation, counters, args.verbose)
                if phase in {"new", "update"}:
                    last_stationary_poll[track_id] = time.monotonic()
                    if evaluation.frame_observed_us is not None:
                        last_stationary_frame[track_id] = evaluation.frame_observed_us
                if (
                    args.max_evaluations is not None
                    and sum(counters.values()) >= args.max_evaluations
                ):
                    stop.set()
                    break
            if workers and not stop.is_set():
                now_monotonic = time.monotonic()
                for track_id, update in tuple(active_updates.items()):
                    if update.get("attributes", {}).get("stationary") is not True:
                        continue
                    if (
                        now_monotonic - last_stationary_poll.get(track_id, 0.0)
                        < args.min_track_interval_seconds
                    ):
                        continue
                    camera_id = str(update.get("camera_id", ""))
                    buffer = buffers.get(camera_id)
                    if buffer is None:
                        continue
                    evaluation = _evaluate_stationary(
                        validator,
                        update,
                        buffer,
                        last_stationary_frame.get(track_id),
                        args.alignment_seconds,
                    )
                    last_stationary_poll[track_id] = now_monotonic
                    if evaluation is None:
                        continue
                    if evaluation.frame_observed_us is not None:
                        last_stationary_frame[track_id] = evaluation.frame_observed_us
                    _record_evaluation(audit, evaluation, counters, args.verbose)
                    if (
                        args.max_evaluations is not None
                        and sum(counters.values()) >= args.max_evaluations
                    ):
                        stop.set()
                        break
            if not rows:
                stop.wait(args.poll_seconds)
            if time.monotonic() >= next_status:
                print(
                    "detector_consensus_shadow_status "
                    + json.dumps(
                        {
                            "cursor": cursor,
                            **counters,
                            "decisions": audit.counts(),
                            "tracks": audit.track_counts(),
                            "streams": [worker.status() for worker in workers.values()],
                        },
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
        audit.close()
        print("detector_consensus_shadow_stopped", flush=True)
    return 0


def _load_capture_config(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "adaptive_capture_config.v1":
        raise ValueError("unsupported adaptive capture config")
    if not isinstance(document.get("cameras"), list):
        raise ValueError("adaptive capture cameras must be a list")
    return document


def _update_observed_us(update: dict, fallback: int) -> int:
    value = update.get("observed_at")
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return round(parsed.timestamp() * 1_000_000)


def _evaluate_live(
    validator: ShadowConsensusValidator,
    update: dict,
    observed_us: int,
    buffer: RollingJpegBuffer,
    alignment_seconds: float,
):
    frames = buffer.window(
        observed_us,
        before_seconds=alignment_seconds,
        after_seconds=0.25,
    )
    if not frames:
        return validator.evaluate_frame(update, None)
    frame = min(frames, key=lambda item: abs(item.observed_us - observed_us))
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError("OpenCV and NumPy are required for live consensus") from error
    image = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    return validator.evaluate_frame(
        update,
        image,
        frame_observed_us=frame.observed_us,
        alignment_delta_us=abs(frame.observed_us - observed_us),
    )


def _evaluate_stationary(
    validator: ShadowConsensusValidator,
    update: dict,
    buffer: RollingJpegBuffer,
    previous_frame_us: int | None,
    alignment_seconds: float,
):
    now_us = time.time_ns() // 1_000
    frames = buffer.window(now_us, before_seconds=alignment_seconds, after_seconds=0)
    if not frames:
        return None
    frame = frames[-1]
    if frame.observed_us == previous_frame_us:
        return None
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError("OpenCV and NumPy are required for live consensus") from error
    image = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    synthetic = dict(update)
    synthetic["message_id"] = f"{update.get('message_id', '')}:shadow:{frame.observed_us}"
    synthetic["phase"] = "update"
    return validator.evaluate_frame(
        synthetic,
        image,
        frame_observed_us=frame.observed_us,
        alignment_delta_us=abs(frame.observed_us - now_us),
    )


def _record_evaluation(
    audit: ConsensusAuditStore,
    evaluation,
    counters: dict[str, int],
    verbose: bool,
) -> None:
    track_state = audit.save(evaluation, time.time_ns() // 1_000)
    key = "evaluated" if evaluation.evaluated else "not_evaluated"
    counters[key] += 1
    if verbose or evaluation.proposed_state != "eligible":
        print(
            "detector_consensus_shadow "
            f"camera={evaluation.camera_id} track={evaluation.track_id} "
            f"proposed={evaluation.proposed_state} reason={evaluation.reason} "
            f"track_state={track_state['state']} iou={evaluation.max_iou} "
            f"latency_ms={evaluation.latency_ms:g}",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
