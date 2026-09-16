"""Incremental reader for the durable receiver SQLite log."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from .store import ProjectionResult, TrackingStore


Validator = Callable[[Any], None]
ProjectionCallback = Callable[[dict[str, Any], ProjectionResult], int]


@dataclass(frozen=True)
class BatchResult:
    scanned: int
    applied: int
    duplicates: int
    last_rowid: int


class TrackingRunner:
    """Incrementally project rows committed by the local receiver."""

    def __init__(
        self,
        receiver_database: Path,
        store: TrackingStore,
        validator: Validator,
        *,
        batch_size: int = 250,
        on_projected: ProjectionCallback | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.receiver_database = receiver_database.resolve()
        self.store = store
        self.validator = validator
        self.batch_size = batch_size
        self.on_projected = on_projected
        self.source_id = str(self.receiver_database).casefold()

    def poll(self) -> BatchResult:
        """Project at most one ordered batch and advance the durable cursor."""
        cursor = self.store.get_cursor(self.source_id)
        rows = self._fetch_after(cursor)
        applied = duplicates = 0
        last_rowid = cursor
        for row in rows:
            update = json.loads(row["payload_json"])
            self.validator(update)
            received_at = datetime.fromisoformat(row["receiver_received_at"])
            result = self.store.project(update, received_at=received_at)
            if result.applied:
                applied += 1
                if self.on_projected is not None:
                    self.on_projected(update, result)
            else:
                duplicates += 1
            last_rowid = int(row["receiver_rowid"])
            self.store.advance_cursor(
                self.source_id,
                last_rowid,
                update["message_id"],
                datetime.now(timezone.utc),
            )
        return BatchResult(
            scanned=len(rows),
            applied=applied,
            duplicates=duplicates,
            last_rowid=last_rowid,
        )

    def pending_count(self) -> int:
        cursor = self.store.get_cursor(self.source_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM track_updates WHERE rowid > ?", (cursor,)
            ).fetchone()
        return int(row[0])

    def _fetch_after(self, cursor: int) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT rowid AS receiver_rowid, payload_json, receiver_received_at
                FROM track_updates
                WHERE rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (cursor, self.batch_size),
            ).fetchall()
        return list(rows)

    def _connect(self) -> sqlite3.Connection:
        if not self.receiver_database.is_file():
            raise FileNotFoundError(
                f"receiver database does not exist: {self.receiver_database}"
            )
        connection = sqlite3.connect(
            f"file:{self.receiver_database.as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
