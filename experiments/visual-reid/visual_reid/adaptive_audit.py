"""Sanitized seven-day audit trail for adaptive capture decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .adaptive_service import ProcessResult


RETENTION_DAYS = 7


class AdaptiveAuditStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS adaptive_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                started_us INTEGER NOT NULL,
                stopped_at TEXT,
                stopped_us INTEGER,
                cameras_json TEXT NOT NULL,
                normal_fps REAL NOT NULL,
                reinforce_fps REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS adaptive_decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                receiver_rowid INTEGER NOT NULL,
                receiver_received_us INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                recorded_us INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                evidence_state TEXT NOT NULL,
                reinforce INTEGER NOT NULL,
                decision_target_fps REAL NOT NULL,
                actual_camera_fps REAL NOT NULL,
                accepted_observations INTEGER NOT NULL,
                reasons_json TEXT NOT NULL,
                UNIQUE(run_id, receiver_rowid),
                FOREIGN KEY(run_id) REFERENCES adaptive_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS adaptive_decisions_time
                ON adaptive_decisions(receiver_received_us);
            """
        )
        self._connection.commit()

    def start_run(
        self,
        cameras: list[str],
        *,
        normal_fps: float,
        reinforce_fps: float,
    ) -> str:
        now = datetime.now(timezone.utc)
        run_id = f"adaptive-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self._connection.execute(
            """
            INSERT INTO adaptive_runs (
                run_id, started_at, started_us, cameras_json,
                normal_fps, reinforce_fps
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                now.isoformat(),
                round(now.timestamp() * 1_000_000),
                json.dumps(sorted(cameras), separators=(",", ":")),
                normal_fps,
                reinforce_fps,
            ),
        )
        self._connection.commit()
        return run_id

    def save_decision(
        self,
        run_id: str,
        receiver_rowid: int,
        receiver_received_us: int,
        message_id: str,
        result: ProcessResult,
        *,
        actual_camera_fps: float,
    ) -> None:
        now = datetime.now(timezone.utc)
        decision = result.decision
        self._connection.execute(
            """
            INSERT OR IGNORE INTO adaptive_decisions (
                run_id, receiver_rowid, receiver_received_us,
                recorded_at, recorded_us, message_id, track_id, camera_id,
                phase, evidence_state, reinforce, decision_target_fps,
                actual_camera_fps, accepted_observations, reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                receiver_rowid,
                receiver_received_us,
                now.isoformat(),
                round(now.timestamp() * 1_000_000),
                message_id,
                result.track_id,
                result.camera_id,
                result.phase,
                decision.state.value,
                int(decision.reinforce),
                decision.target_fps,
                actual_camera_fps,
                result.accepted_observations,
                json.dumps(decision.reasons, separators=(",", ":")),
            ),
        )
        self._connection.commit()

    def finish_run(self, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        self._connection.execute(
            """
            UPDATE adaptive_runs SET stopped_at=?, stopped_us=? WHERE run_id=?
            """,
            (now.isoformat(), round(now.timestamp() * 1_000_000), run_id),
        )
        self._connection.commit()

    def cleanup(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff_us = round(
            (current - timedelta(days=RETENTION_DAYS)).timestamp() * 1_000_000
        )
        cursor = self._connection.execute(
            "DELETE FROM adaptive_decisions WHERE receiver_received_us < ?",
            (cutoff_us,),
        )
        self._connection.execute(
            """
            DELETE FROM adaptive_runs
            WHERE started_us < ?
              AND NOT EXISTS (
                  SELECT 1 FROM adaptive_decisions d
                  WHERE d.run_id = adaptive_runs.run_id
              )
            """,
            (cutoff_us,),
        )
        self._connection.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> AdaptiveAuditStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
