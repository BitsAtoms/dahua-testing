"""SQLite-backed MQTT outbox for ``track_update.v1`` messages."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


StatusCallback = Callable[[str], None]


class OutboxStore:
    """Persist messages until a broker acknowledges their publication."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_updates (
                message_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                enqueued_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_enqueued_at "
            "ON pending_updates(enqueued_at)"
        )
        self._connection.commit()

    def enqueue(self, update: dict[str, Any]) -> bool:
        message_id = update.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("outbox message requires a non-empty message_id")
        payload = json.dumps(
            update,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self._connection.execute(
                "SELECT payload FROM pending_updates WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError(f"outbox message_id collision: {message_id}")
                return False
            self._connection.execute(
                "INSERT INTO pending_updates(message_id, payload, enqueued_at) "
                "VALUES (?, ?, ?)",
                (message_id, payload, now),
            )
            self._connection.commit()
        return True

    def pending(self, limit: int = 100) -> list[tuple[str, bytes]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT message_id, payload FROM pending_updates "
                "ORDER BY enqueued_at, message_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [(message_id, payload.encode("utf-8")) for message_id, payload in rows]

    def delivered(self, message_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM pending_updates WHERE message_id = ?",
                (message_id,),
            )
            self._connection.commit()

    def failed(self, message_id: str, error: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE pending_updates SET attempts = attempts + 1, last_error = ? "
                "WHERE message_id = ?",
                (error[:1000], message_id),
            )
            self._connection.commit()

    def cleanup(self, *, days: int = 7, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=days)).isoformat()
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM pending_updates WHERE enqueued_at < ?",
                (cutoff,),
            )
            self._connection.commit()
        return cursor.rowcount

    def count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM pending_updates"
            ).fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class MqttOutboxPublisher:
    """Publish a durable outbox in the background with MQTT QoS 1."""

    def __init__(
        self,
        store: OutboxStore,
        *,
        host: str,
        port: int,
        topic: str,
        client_id: str,
        username: str = "",
        password: str = "",
        status: StatusCallback = print,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as error:
            raise RuntimeError("paho-mqtt is required for track transport") from error

        self.store = store
        self.topic = topic
        self._status = status
        self._mqtt = mqtt
        self._connected = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        if username:
            self._client.username_pw_set(username, password)

        def on_connect(
            _client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            if reason_code == 0:
                self._connected.set()
                self._wake.set()
                self._status(f"track_transport_connected topic={self.topic} qos=1")
            else:
                self._status(f"track_transport_connection_failed reason={reason_code}")

        def on_disconnect(
            _client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            self._connected.clear()
            if not self._stop.is_set():
                self._status(f"track_transport_disconnected reason={reason_code}")

        self._client.on_connect = on_connect
        self._client.on_disconnect = on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.connect_async(host, port, keepalive=60)
        self._worker = threading.Thread(
            target=self._run,
            name="track-mqtt-outbox",
            daemon=True,
        )

    def start(self) -> None:
        self._client.loop_start()
        self._worker.start()
        self._wake.set()

    def publish(self, update: dict[str, Any]) -> bool:
        inserted = self.store.enqueue(update)
        self._wake.set()
        return inserted

    def _run(self) -> None:
        next_cleanup = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() >= next_cleanup:
                removed = self.store.cleanup(days=7)
                if removed:
                    self._status(f"track_transport_expired count={removed}")
                next_cleanup = time.monotonic() + 3600

            if not self._connected.wait(timeout=0.5):
                continue
            rows = self.store.pending()
            if not rows:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue

            for message_id, payload in rows:
                if self._stop.is_set() or not self._connected.is_set():
                    break
                try:
                    publication = self._client.publish(
                        self.topic,
                        payload=payload,
                        qos=1,
                        retain=False,
                    )
                    publication.wait_for_publish(timeout=10)
                    if not publication.is_published():
                        raise TimeoutError("MQTT PUBACK timeout")
                except Exception as error:
                    self.store.failed(message_id, str(error))
                    self._status(
                        f"track_transport_retry message_id={message_id} error={error}"
                    )
                    self._connected.wait(timeout=1)
                    break
                self.store.delivered(message_id)
                self._status(f"track_transport_delivered message_id={message_id}")

    def stop(self, timeout: float = 12) -> None:
        self._stop.set()
        self._wake.set()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise RuntimeError("track transport worker did not stop cleanly")
        self._client.disconnect()
        self._client.loop_stop()
        pending = self.store.count()
        self._status(f"track_transport_stopped pending={pending}")
        self.store.close()
