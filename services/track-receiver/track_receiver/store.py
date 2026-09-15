"""SQLite persistence and idempotent ingestion for normalized track updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

from .validation import validate_track_update


RETENTION_DAYS = 7
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class IngestResult:
    inserted: bool
    message_id: str
    received_at: str
    ingest_ms: float


class ReceiverStore:
    """Own one SQLite connection and persist complete provider-neutral messages."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def ingest(
        self, update: dict[str, Any], received_at: datetime | None = None
    ) -> IngestResult:
        """Validate and insert one message, returning a duplicate-safe result."""
        started_ns = time.perf_counter_ns()
        validate_track_update(update)
        received = _as_utc(received_at or datetime.now(timezone.utc))
        received_text = received.isoformat()
        received_us = round(received.timestamp() * 1_000_000)
        wire = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
        try:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO track_updates (
                    message_id, track_id, source_type, source_instance_id,
                    camera_id, phase, sequence, observed_at, published_at,
                    receiver_received_at, receiver_received_us, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    update["message_id"],
                    update["track_id"],
                    update["source"]["type"],
                    update["source"]["instance_id"],
                    update["camera_id"],
                    update["phase"],
                    update["sequence"],
                    update["observed_at"],
                    update["published_at"],
                    received_text,
                    received_us,
                    wire,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return IngestResult(
            inserted=cursor.rowcount == 1,
            message_id=update["message_id"],
            received_at=received_text,
            ingest_ms=round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        )

    def cleanup(
        self, now: datetime | None = None, retention_days: int = RETENTION_DAYS
    ) -> int:
        """Delete received messages older than the configured retention window."""
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        current = _as_utc(now or datetime.now(timezone.utc))
        cutoff = current - timedelta(days=retention_days)
        updates = self._connection.execute(
            "DELETE FROM track_updates WHERE receiver_received_us < ?",
            (round(cutoff.timestamp() * 1_000_000),),
        )
        rejected = self._connection.execute(
            "DELETE FROM rejected_updates WHERE receiver_received_us < ?",
            (round(cutoff.timestamp() * 1_000_000),),
        )
        self._connection.commit()
        return updates.rowcount + rejected.rowcount

    def reject(
        self,
        topic: str,
        payload: bytes,
        error: str,
        received_at: datetime | None = None,
    ) -> bool:
        """Persist one poison message once so MQTT can safely acknowledge it."""
        received = _as_utc(received_at or datetime.now(timezone.utc))
        fingerprint = hashlib.sha256(topic.encode("utf-8") + b"\0" + payload).hexdigest()
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO rejected_updates (
                fingerprint, topic, receiver_received_at, receiver_received_us,
                error, payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                topic,
                received.isoformat(),
                round(received.timestamp() * 1_000_000),
                error[:500],
                payload,
            ),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM track_updates"
        ).fetchone()
        return int(row["count"])

    def rejected_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM rejected_updates"
        ).fetchone()
        return int(row["count"])

    def iter_updates(self) -> Iterator[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM track_updates
            ORDER BY receiver_received_us, rowid
            """
        )
        for row in rows:
            yield json.loads(row["payload_json"])

    def summary(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT source_type, camera_id, phase, COUNT(*) AS messages
            FROM track_updates
            GROUP BY source_type, camera_id, phase
            ORDER BY source_type, camera_id, phase
            """
        )
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ReceiverStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported receiver database version: {version}")
        if version == 0:
            self._connection.executescript(
                """
                CREATE TABLE track_updates (
                    message_id TEXT PRIMARY KEY,
                    track_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_instance_id TEXT,
                    camera_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    observed_at TEXT,
                    published_at TEXT NOT NULL,
                    receiver_received_at TEXT NOT NULL,
                    receiver_received_us INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX track_updates_track_sequence
                    ON track_updates(track_id, sequence);
                CREATE INDEX track_updates_camera_received
                    ON track_updates(camera_id, receiver_received_us);
                CREATE INDEX track_updates_retention
                    ON track_updates(receiver_received_us);
                PRAGMA user_version=1;
                """
            )
            self._connection.commit()
            version = 1
        if version == 1:
            self._connection.executescript(
                """
                CREATE TABLE rejected_updates (
                    fingerprint TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    receiver_received_at TEXT NOT NULL,
                    receiver_received_us INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX rejected_updates_retention
                    ON rejected_updates(receiver_received_us);
                PRAGMA user_version=2;
                """
            )
            self._connection.commit()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("receiver timestamp must include a timezone")
    return value.astimezone(timezone.utc)
