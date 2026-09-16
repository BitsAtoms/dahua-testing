"""Seven-day controlled-test sessions with ground-truth annotations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any


RETENTION_DAYS = 7
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
VERDICTS = {"same_person", "different_person", "uncertain"}


class ValidationError(ValueError):
    pass


class ValidationStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS validation_sessions (
                    session_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    expected_route_json TEXT NOT NULL,
                    subjects_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'complete')),
                    started_at TEXT NOT NULL,
                    started_us INTEGER NOT NULL,
                    ended_at TEXT,
                    ended_us INTEGER,
                    report_json TEXT,
                    annotations_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_validation_session
                    ON validation_sessions(status) WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS validation_session_retention
                    ON validation_sessions(ended_us);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def start(
        self,
        name: str,
        expected_route: list[str],
        subjects: list[str],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 120:
            raise ValidationError("name must be 1 to 120 characters")
        route = _text_list(expected_route, "expected_route", 64)
        aliases = _text_list(subjects, "subjects", 32)
        if not route:
            raise ValidationError("expected_route needs at least one camera")
        if not aliases or any(not ALIAS_RE.fullmatch(item) for item in aliases):
            raise ValidationError("subjects must use short aliases such as A or B")
        if len(set(aliases)) != len(aliases):
            raise ValidationError("subject aliases must be unique")
        current = _utc(now or datetime.now(timezone.utc))
        session_id = (
            current.strftime("test-%Y%m%dT%H%M%SZ-") + secrets.token_hex(3)
        )
        connection = self._connect()
        try:
            if connection.execute(
                "SELECT 1 FROM validation_sessions WHERE status = 'active'"
            ).fetchone():
                raise ValidationError("another validation session is already active")
            connection.execute(
                """
                INSERT INTO validation_sessions (
                    session_id, name, expected_route_json, subjects_json,
                    status, started_at, started_us, annotations_json
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, '{}')
                """,
                (
                    session_id,
                    name,
                    _json(route),
                    _json(aliases),
                    current.isoformat(),
                    round(current.timestamp() * 1_000_000),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(session_id)  # type: ignore[return-value]

    def complete(
        self,
        session_id: str,
        report: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc(now or datetime.now(timezone.utc))
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE validation_sessions
                SET status='complete', ended_at=?, ended_us=?, report_json=?
                WHERE session_id=? AND status='active'
                """,
                (
                    current.isoformat(),
                    round(current.timestamp() * 1_000_000),
                    _json(report),
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValidationError("active validation session not found")
            connection.commit()
        finally:
            connection.close()
        return self.get(session_id)  # type: ignore[return-value]

    def annotate(self, session_id: str, document: dict[str, Any]) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            raise ValidationError("validation session not found")
        report = session.get("report") or {}
        valid_tracks = {item["track_id"] for item in report.get("tracks", [])}
        valid_candidates = {
            item["candidate_id"] for item in report.get("candidates", [])
        }
        track_subjects = document.get("track_subjects", {})
        candidate_verdicts = document.get("candidate_verdicts", {})
        notes = str(document.get("notes", "")).strip()
        if not isinstance(track_subjects, dict) or not isinstance(
            candidate_verdicts, dict
        ):
            raise ValidationError("annotations must be objects")
        if set(track_subjects) - valid_tracks:
            raise ValidationError("track annotation references an unknown track")
        if set(candidate_verdicts) - valid_candidates:
            raise ValidationError("candidate annotation references an unknown candidate")
        aliases = set(session["subjects"])
        if any(value not in aliases for value in track_subjects.values()):
            raise ValidationError("track annotation uses an unknown subject alias")
        if any(value not in VERDICTS for value in candidate_verdicts.values()):
            raise ValidationError("candidate verdict is invalid")
        if len(notes) > 2000:
            raise ValidationError("notes must be at most 2000 characters")
        annotations = {
            "track_subjects": track_subjects,
            "candidate_verdicts": candidate_verdicts,
            "notes": notes,
        }
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE validation_sessions SET annotations_json=? WHERE session_id=?",
                (_json(annotations), session_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(session_id)  # type: ignore[return-value]

    def active(self) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM validation_sessions WHERE status='active'"
            ).fetchone()
            return _row(row) if row else None
        finally:
            connection.close()

    def get(self, session_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM validation_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return _row(row) if row else None
        finally:
            connection.close()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM validation_sessions
                ORDER BY started_us DESC LIMIT ?
                """,
                (limit,),
            )
            return [_row(row) for row in rows]
        finally:
            connection.close()

    def cleanup(self, now: datetime | None = None) -> int:
        cutoff = _utc(now or datetime.now(timezone.utc)) - timedelta(
            days=RETENTION_DAYS
        )
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                DELETE FROM validation_sessions
                WHERE status='complete' AND ended_us < ?
                """,
                (round(cutoff.timestamp() * 1_000_000),),
            )
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "name": row["name"],
        "expected_route": json.loads(row["expected_route_json"]),
        "subjects": json.loads(row["subjects_json"]),
        "status": row["status"],
        "started_at": row["started_at"],
        "started_us": row["started_us"],
        "ended_at": row["ended_at"],
        "ended_us": row["ended_us"],
        "report": json.loads(row["report_json"]) if row["report_json"] else None,
        "annotations": json.loads(row["annotations_json"]),
    }


def _text_list(value: Any, name: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{name} must be a list of text values")
    result = [item.strip() for item in value]
    if any(not item or len(item) > maximum for item in result):
        raise ValidationError(f"{name} contains an invalid value")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationError("validation timestamp must include a timezone")
    return value.astimezone(timezone.utc)
