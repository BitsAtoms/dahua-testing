#!/usr/bin/env python3
"""Consume Frigate MQTT events and write common tracking updates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACK_TRANSPORT_ROOT = REPOSITORY_ROOT / "services" / "track-transport"
sys.path.insert(0, str(TRACK_TRANSPORT_ROOT))

from frigate_adapter import FrigateEventAdapter
from frigate_adapter.retention import remove_expired_files
from frigate_adapter.snapshots import SnapshotEnricher
from frigate_adapter.timing import build_pipeline_timing
from track_transport import MqttOutboxPublisher, OutboxStore


QueueItem = tuple[str, bytes, str, int]


def read_env(path: Path) -> dict[str, str]:
    """Read a simple dotenv file without exporting secrets to the process."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line_number, original in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"malformed .env line {line_number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_frame_sizes(value: str) -> dict[str, tuple[int, int]]:
    """Parse ``camera=WIDTHxHEIGHT`` entries separated by commas."""
    sizes: dict[str, tuple[int, int]] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        try:
            camera, dimensions = item.strip().split("=", 1)
            width_text, height_text = dimensions.lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except ValueError as error:
            raise ValueError(
                "FRIGATE_CAMERA_FRAME_SIZES must use camera=WIDTHxHEIGHT"
            ) from error
        if not camera or width <= 0 or height <= 0:
            raise ValueError(
                "FRIGATE_CAMERA_FRAME_SIZES must use camera=WIDTHxHEIGHT"
            )
        sizes[camera] = (width, height)
    if not sizes:
        raise ValueError("FRIGATE_CAMERA_FRAME_SIZES is required")
    return sizes


def merged_config(env_file: Path) -> dict[str, str]:
    """Merge dotenv values with process environment taking precedence."""
    return {**read_env(env_file), **os.environ}


def require(config: dict[str, str], key: str) -> str:
    """Return one required non-empty configuration value."""
    value = config.get(key, "")
    if not value:
        raise ValueError(f"missing required configuration: {key}")
    return value


def append_json_line(output: Any, payload: dict[str, Any]) -> None:
    """Append and flush one compact JSON record."""
    output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    output.write("\n")
    output.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/frigate-adapter/output"),
    )
    parser.add_argument("--queue-size", type=int, default=1000)
    parser.add_argument("--outbox-database", type=Path)
    args = parser.parse_args()
    if args.queue_size <= 0:
        raise ValueError("queue size must be positive")

    config = merged_config(args.env_file)
    host = require(config, "FRIGATE_MQTT_HOST")
    port = int(config.get("FRIGATE_MQTT_PORT", "1883"))
    topic_prefix = config.get("FRIGATE_MQTT_TOPIC_PREFIX", "frigate")
    topic = f"{topic_prefix}/events"
    instance_id = config.get("FRIGATE_INSTANCE_ID", "local-frigate")
    frame_sizes = parse_frame_sizes(require(config, "FRIGATE_CAMERA_FRAME_SIZES"))
    adapter = FrigateEventAdapter(frame_sizes, instance_id=instance_id)
    snapshot_api_url = config.get("FRIGATE_API_URL", "")
    track_host = config.get("TRACK_MQTT_HOST") or host
    track_port = int(config.get("TRACK_MQTT_PORT", str(port)))
    track_topic = config.get("TRACK_MQTT_TOPIC", "tracking/track-updates")
    outbox_path = args.outbox_database or Path(
        config.get(
            "TRACK_OUTBOX_DATABASE",
            "runtime/track-outbox/frigate.sqlite3",
        )
    )

    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "paho-mqtt is missing; install experiments/frigate-adapter/requirements.txt"
        ) from error

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    removed_files, removed_bytes = remove_expired_files(output_root)
    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_root = output_root / session
    session_root.mkdir()
    snapshot_enricher = (
        SnapshotEnricher(adapter, snapshot_api_url, session_root / "media")
        if snapshot_api_url
        else None
    )
    track_publisher = MqttOutboxPublisher(
        OutboxStore(outbox_path),
        host=track_host,
        port=track_port,
        topic=track_topic,
        client_id=f"track-publisher-{instance_id}",
        username=config.get("TRACK_MQTT_USER")
        or config.get("FRIGATE_MQTT_USER", ""),
        password=config.get("TRACK_MQTT_PASSWORD")
        or config.get("FRIGATE_MQTT_PASSWORD", ""),
    )

    messages: queue.Queue[QueueItem] = queue.Queue(maxsize=args.queue_size)
    stop = threading.Event()
    overflow = threading.Event()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"track-adapter-{instance_id}",
    )
    username = config.get("FRIGATE_MQTT_USER")
    if username:
        client.username_pw_set(username, config.get("FRIGATE_MQTT_PASSWORD"))

    def on_connect(
        connected_client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if reason_code != 0:
            print(f"mqtt_connection_failed reason={reason_code}", flush=True)
            return
        connected_client.subscribe(topic, qos=0)
        print(f"mqtt_connected topic={topic}", flush=True)

    def on_disconnect(
        _client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if not stop.is_set():
            print(f"mqtt_disconnected reason={reason_code}", flush=True)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        received_at = datetime.now(timezone.utc).isoformat()
        received_ns = time.perf_counter_ns()
        try:
            messages.put_nowait(
                (message.topic, bytes(message.payload), received_at, received_ns)
            )
        except queue.Full:
            overflow.set()
            stop.set()
            print("fatal=event_queue_full", flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    def request_stop(_signal_number: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    raw_path = session_root / "raw-events.jsonl"
    updates_path = session_root / "track-updates.jsonl"
    timings_path = session_root / "pipeline-timings.jsonl"
    print(f"output={session_root}")
    print(f"retention_removed files={removed_files} bytes={removed_bytes}")
    print(f"snapshots={'enabled' if snapshot_enricher else 'disabled'}")
    print(f"track_outbox={outbox_path.resolve()}")
    track_publisher.start()
    client.connect(host, port, keepalive=60)
    client.loop_start()
    next_retention = time.monotonic() + 3600

    try:
        with (
            raw_path.open("a", encoding="utf-8", newline="\n") as raw_output,
            updates_path.open("a", encoding="utf-8", newline="\n") as update_output,
            timings_path.open("a", encoding="utf-8", newline="\n") as timing_output,
        ):
            def drain_snapshots() -> None:
                if snapshot_enricher is None:
                    return
                for result in snapshot_enricher.drain():
                    if result.update is None:
                        print(
                            "snapshot_failed "
                            f"camera={result.camera_id} "
                            f"track={result.local_track_id} "
                            f"error={result.error}",
                            flush=True,
                        )
                        continue
                    append_json_line(update_output, result.update)
                    track_publisher.publish(result.update)
                    print(
                        "snapshot_update "
                        f"camera={result.camera_id} "
                        f"track={result.local_track_id} "
                        f"bytes={result.byte_count}",
                        flush=True,
                    )

            while (
                not stop.is_set()
                or not messages.empty()
                or (snapshot_enricher is not None and snapshot_enricher.pending_count)
            ):
                drain_snapshots()
                try:
                    (
                        received_topic,
                        wire_payload,
                        received_at,
                        received_ns,
                    ) = messages.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() >= next_retention:
                        remove_expired_files(output_root)
                        next_retention = time.monotonic() + 3600
                    continue

                dequeued_ns = time.perf_counter_ns()
                try:
                    payload = json.loads(wire_payload)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    print(f"invalid_mqtt_json error={error}", flush=True)
                    continue
                decoded_ns = time.perf_counter_ns()
                append_json_line(
                    raw_output,
                    {
                        "received_at": received_at,
                        "topic": received_topic,
                        "payload": payload,
                    },
                )
                raw_persisted_ns = time.perf_counter_ns()
                normalize_started_ns = raw_persisted_ns
                try:
                    update = adapter.adapt(payload)
                except ValueError as error:
                    print(f"invalid_frigate_event error={error}", flush=True)
                    continue
                normalized_ns = time.perf_counter_ns()
                if update is None:
                    continue
                append_json_line(update_output, update)
                track_publisher.publish(update)
                output_persisted_at = datetime.now(timezone.utc).isoformat()
                output_persisted_ns = time.perf_counter_ns()
                timing = build_pipeline_timing(
                    update,
                    mqtt_received_at=received_at,
                    output_persisted_at=output_persisted_at,
                    received_ns=received_ns,
                    dequeued_ns=dequeued_ns,
                    decoded_ns=decoded_ns,
                    raw_persisted_ns=raw_persisted_ns,
                    normalize_started_ns=normalize_started_ns,
                    normalized_ns=normalized_ns,
                    output_persisted_ns=output_persisted_ns,
                )
                append_json_line(timing_output, timing)
                durations = timing["duration_ms"]
                print(
                    "track_update "
                    f"camera={update['camera_id']} "
                    f"phase={update['phase']} "
                    f"track={update['subject']['local_track_id']} "
                    f"event_age={durations['event_age_at_mqtt']:.1f}ms "
                    f"queue={durations['queue_wait']:.1f}ms "
                    f"normalize={durations['normalize']:.1f}ms "
                    f"mqtt_to_output={durations['mqtt_callback_to_output']:.1f}ms",
                    flush=True,
                )
                after = payload.get("after", {})
                snapshot = after.get("snapshot") or {}
                snapshot_timestamp = snapshot.get("frame_time")
                if (
                    snapshot_enricher is not None
                    and update["phase"] == "end"
                    and after.get("has_snapshot") is True
                    and isinstance(snapshot_timestamp, (int, float))
                ):
                    if not snapshot_enricher.submit(update, float(snapshot_timestamp)):
                        print(
                            "snapshot_queue_full "
                            f"camera={update['camera_id']} "
                            f"track={update['subject']['local_track_id']}",
                            flush=True,
                        )
            drain_snapshots()
    finally:
        stop.set()
        if snapshot_enricher is not None:
            snapshot_enricher.shutdown()
        client.disconnect()
        client.loop_stop()
        track_publisher.stop()
        print("mqtt_runner_stopped", flush=True)
    return 2 if overflow.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
