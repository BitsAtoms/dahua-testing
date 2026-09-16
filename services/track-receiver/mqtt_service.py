#!/usr/bin/env python3
"""Receive track_update.v1 messages from local MQTT and commit them to SQLite."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import signal
import threading
import time
from typing import Any

from track_receiver import ContractError, ReceiverStore
from track_receiver.configuration import merged_config, mqtt_config


MAX_MESSAGE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class QueueItem:
    topic: str
    payload: bytes
    received_at: datetime
    received_ns: int
    message_id: int
    qos: int


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument("--queue-size", type=int, default=1000)
    args = parser.parse_args()
    if args.queue_size <= 0:
        raise ValueError("queue size must be positive")

    config = merged_config(args.env_file)
    transport = mqtt_config(config)
    receiver_id = config.get("TRACK_RECEIVER_ID", "local-track-receiver")

    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "paho-mqtt is missing; install services/track-receiver/requirements.txt"
        ) from error

    messages: queue.Queue[QueueItem] = queue.Queue(maxsize=args.queue_size)
    stop = threading.Event()
    overflow = threading.Event()
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=receiver_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    client.manual_ack_set(True)
    if transport["username"]:
        client.username_pw_set(
            str(transport["username"]), str(transport["password"])
        )
    client.reconnect_delay_set(min_delay=1, max_delay=30)

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
        connected_client.subscribe(str(transport["topic"]), qos=1)
        print(f"mqtt_connected topic={transport['topic']} qos=1", flush=True)

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
        item = QueueItem(
            topic=message.topic,
            payload=bytes(message.payload),
            received_at=datetime.now(timezone.utc),
            received_ns=time.perf_counter_ns(),
            message_id=message.mid,
            qos=message.qos,
        )
        try:
            messages.put_nowait(item)
        except queue.Full:
            overflow.set()
            stop.set()
            print("fatal=receiver_queue_full", flush=True)

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

    database = args.database.resolve()
    print(f"receiver_database={database}")
    print(f"receiver_id={receiver_id}")
    client.connect(str(transport["host"]), int(transport["port"]), keepalive=60)
    client.loop_start()
    next_cleanup = time.monotonic()

    try:
        with ReceiverStore(database) as store:
            while not stop.is_set() or not messages.empty():
                if time.monotonic() >= next_cleanup:
                    removed = store.cleanup()
                    if removed:
                        print(f"retention_removed={removed}", flush=True)
                    next_cleanup = time.monotonic() + 3600
                try:
                    item = messages.get(timeout=0.5)
                except queue.Empty:
                    continue

                if len(item.payload) > MAX_MESSAGE_BYTES:
                    error = f"message exceeds {MAX_MESSAGE_BYTES} bytes"
                    store.reject(item.topic, item.payload, error, item.received_at)
                    _acknowledge(client, item)
                    print(f"rejected error={error}", flush=True)
                    continue
                try:
                    update = json.loads(item.payload)
                    result = store.ingest(update, item.received_at)
                except (json.JSONDecodeError, UnicodeDecodeError, ContractError, ValueError) as error:
                    detail = str(error).replace("\r", " ").replace("\n", " ")[:240]
                    store.reject(item.topic, item.payload, detail, item.received_at)
                    _acknowledge(client, item)
                    print(f"rejected error={detail}", flush=True)
                    continue

                _acknowledge(client, item)
                callback_to_store_ms = round(
                    (time.perf_counter_ns() - item.received_ns) / 1_000_000, 3
                )
                print(
                    "track_update_received "
                    f"source={update['source']['type']} "
                    f"camera={update['camera_id']} "
                    f"phase={update['phase']} "
                    f"track={update['subject']['local_track_id']} "
                    f"inserted={str(result.inserted).lower()} "
                    f"store={result.ingest_ms:.3f}ms "
                    f"callback_to_store={callback_to_store_ms:.3f}ms",
                    flush=True,
                )
    finally:
        stop.set()
        client.disconnect()
        client.loop_stop()
        print("track_receiver_stopped", flush=True)
    return 2 if overflow.is_set() else 0


def _acknowledge(client: Any, item: QueueItem) -> None:
    if item.qos > 0:
        result = client.ack(item.message_id, item.qos)
        if result != 0:
            print(f"mqtt_ack_failed code={result}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
