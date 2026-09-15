"""Durable projection of ``track_update.v1`` messages into local tracks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


RETENTION_DAYS = 7
STALE_TRACK_SECONDS = 120
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ProjectionResult:
    applied: bool
    message_id: str
    track_id: str
    status: str


class TrackingStore:
    """Own the rebuildable state derived from the durable receiver log."""

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

    def project(
        self,
        update: dict[str, Any],
        received_at: datetime | None = None,
    ) -> ProjectionResult:
        """Apply one already validated update exactly once."""
        received = _as_utc(received_at or datetime.now(timezone.utc))
        received_text = received.isoformat()
        received_us = round(received.timestamp() * 1_000_000)
        message_id = update["message_id"]
        track_id = update["track_id"]

        try:
            inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO processed_updates (
                    message_id, track_id, processed_at, processed_us
                ) VALUES (?, ?, ?, ?)
                """,
                (message_id, track_id, received_text, received_us),
            )
            if inserted.rowcount == 0:
                row = self._track_row(track_id)
                self._connection.commit()
                return ProjectionResult(
                    applied=False,
                    message_id=message_id,
                    track_id=track_id,
                    status=str(row["status"]) if row else "unknown",
                )

            current = self._track_row(track_id)
            projected = _project_state(current, update, received_text, received_us)
            self._connection.execute(
                """
                INSERT INTO local_tracks (
                    track_id, source_type, source_instance_id, camera_id,
                    local_track_id, subject_type, status, end_reason,
                    source_lifecycle,
                    first_observed_at, last_observed_at, ended_at,
                    first_received_at, last_received_at, last_received_us,
                    last_phase, max_sequence, confidence, geometry_json,
                    zones_json, attributes_json, media_json, quality_status
                ) VALUES (
                    :track_id, :source_type, :source_instance_id, :camera_id,
                    :local_track_id, :subject_type, :status, :end_reason,
                    :source_lifecycle,
                    :first_observed_at, :last_observed_at, :ended_at,
                    :first_received_at, :last_received_at, :last_received_us,
                    :last_phase, :max_sequence, :confidence, :geometry_json,
                    :zones_json, :attributes_json, :media_json, :quality_status
                )
                ON CONFLICT(track_id) DO UPDATE SET
                    status=excluded.status,
                    end_reason=excluded.end_reason,
                    source_lifecycle=excluded.source_lifecycle,
                    first_observed_at=excluded.first_observed_at,
                    last_observed_at=excluded.last_observed_at,
                    ended_at=excluded.ended_at,
                    last_received_at=excluded.last_received_at,
                    last_received_us=excluded.last_received_us,
                    last_phase=excluded.last_phase,
                    max_sequence=excluded.max_sequence,
                    confidence=excluded.confidence,
                    geometry_json=excluded.geometry_json,
                    zones_json=excluded.zones_json,
                    attributes_json=excluded.attributes_json,
                    media_json=excluded.media_json,
                    quality_status=excluded.quality_status
                """,
                projected,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return ProjectionResult(
            applied=True,
            message_id=message_id,
            track_id=track_id,
            status=projected["status"],
        )

    def get_track(self, track_id: str) -> dict[str, Any] | None:
        row = self._track_row(track_id)
        if row is None:
            return None
        result = dict(row)
        for field in ("geometry", "zones", "attributes", "media"):
            result[field] = json.loads(result.pop(f"{field}_json"))
        return result

    def list_tracks(
        self, *, status: str | None = None, camera_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if camera_id is not None:
            clauses.append("camera_id = ?")
            parameters.append(camera_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT track_id FROM local_tracks{where} "
            "ORDER BY last_received_us DESC, track_id",
            parameters,
        )
        return [self.get_track(str(row["track_id"])) for row in rows]  # type: ignore[misc]

    def cleanup(
        self, now: datetime | None = None, retention_days: int = RETENTION_DAYS
    ) -> int:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        cutoff = _as_utc(now or datetime.now(timezone.utc)) - timedelta(
            days=retention_days
        )
        cutoff_us = round(cutoff.timestamp() * 1_000_000)
        tracks = self._connection.execute(
            "DELETE FROM local_tracks WHERE last_received_us < ?", (cutoff_us,)
        )
        updates = self._connection.execute(
            "DELETE FROM processed_updates WHERE processed_us < ?", (cutoff_us,)
        )
        self._connection.commit()
        return tracks.rowcount + updates.rowcount

    def expire_stale(
        self,
        now: datetime | None = None,
        idle_seconds: int = STALE_TRACK_SECONDS,
    ) -> int:
        """Close live tracks whose source stopped updating without an ``end``."""
        if idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        current = _as_utc(now or datetime.now(timezone.utc))
        cutoff_us = round(
            (current - timedelta(seconds=idle_seconds)).timestamp() * 1_000_000
        )
        cursor = self._connection.execute(
            """
            UPDATE local_tracks
            SET status = 'ended',
                ended_at = last_observed_at,
                end_reason = 'timeout'
            WHERE status = 'active' AND last_received_us < ?
            """,
            (cutoff_us,),
        )
        self._connection.commit()
        return cursor.rowcount

    def count(self) -> int:
        return int(
            self._connection.execute("SELECT COUNT(*) FROM local_tracks").fetchone()[0]
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> TrackingStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _track_row(self, track_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM local_tracks WHERE track_id = ?", (track_id,)
        ).fetchone()

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported tracking database version: {version}")
        if version == 0:
            self._connection.executescript(
                """
                CREATE TABLE local_tracks (
                    track_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_instance_id TEXT,
                    camera_id TEXT NOT NULL,
                    local_track_id TEXT,
                    subject_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'ended')),
                    source_lifecycle TEXT NOT NULL,
                    first_observed_at TEXT,
                    last_observed_at TEXT,
                    ended_at TEXT,
                    first_received_at TEXT NOT NULL,
                    last_received_at TEXT NOT NULL,
                    last_received_us INTEGER NOT NULL,
                    last_phase TEXT NOT NULL,
                    max_sequence INTEGER NOT NULL,
                    confidence REAL,
                    geometry_json TEXT NOT NULL,
                    zones_json TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    quality_status TEXT NOT NULL
                );
                CREATE INDEX local_tracks_status_received
                    ON local_tracks(status, last_received_us);
                CREATE INDEX local_tracks_camera_received
                    ON local_tracks(camera_id, last_received_us);
                CREATE TABLE processed_updates (
                    message_id TEXT PRIMARY KEY,
                    track_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    processed_us INTEGER NOT NULL
                );
                CREATE INDEX processed_updates_retention
                    ON processed_updates(processed_us);
                PRAGMA user_version=1;
                """
            )
            self._connection.commit()
            version = 1
        if version == 1:
            self._connection.executescript(
                """
                ALTER TABLE local_tracks ADD COLUMN end_reason TEXT;
                PRAGMA user_version=2;
                """
            )
            self._connection.commit()


def _project_state(
    current: sqlite3.Row | None,
    update: dict[str, Any],
    received_text: str,
    received_us: int,
) -> dict[str, Any]:
    phase = update["phase"]
    lifecycle = update["quality"]["source_lifecycle"]
    observed_at = update["observed_at"] or update["published_at"]
    current_status = str(current["status"]) if current else None
    current_end_reason = (
        str(current["end_reason"])
        if current and current["end_reason"] is not None
        else None
    )
    ends_track = phase == "end" or (
        phase == "snapshot" and lifecycle == "finalized_only"
    )
    definitive_end = current_status == "ended" and current_end_reason != "timeout"
    status = "ended" if ends_track or definitive_end else "active"
    end_reason = current_end_reason
    if phase == "end":
        end_reason = "source_end"
    elif phase == "snapshot" and lifecycle == "finalized_only":
        end_reason = "source_finalized"
    elif phase in {"new", "update"} and current_end_reason == "timeout":
        end_reason = None

    current_first = str(current["first_observed_at"]) if current else None
    current_last = str(current["last_observed_at"]) if current else None
    first_observed_at = _earliest_timestamp(current_first, observed_at)
    last_observed_at = _latest_timestamp(
        current_last, observed_at
    )
    is_latest_observation = current_last is None or datetime.fromisoformat(
        observed_at
    ) >= datetime.fromisoformat(current_last)
    ended_at = str(current["ended_at"]) if current and current["ended_at"] else None
    if phase in {"new", "update"} and current_end_reason == "timeout":
        ended_at = None
    elif ends_track and ended_at is None:
        ended_at = observed_at

    geometry = update["geometry"]
    if current and (geometry is None or not is_latest_observation):
        geometry_json = str(current["geometry_json"])
    else:
        geometry_json = _json(geometry)

    old_attributes = json.loads(current["attributes_json"]) if current else {}
    attributes = {**old_attributes, **update["attributes"]}
    old_media = json.loads(current["media_json"]) if current else []
    media = _merge_media(old_media, update["media"])
    confidence = update["subject"]["confidence"]
    if current and (confidence is None or not is_latest_observation):
        confidence = current["confidence"]
    zones_json = (
        str(current["zones_json"])
        if current and not is_latest_observation
        else _json(update["zones"])
    )

    return {
        "track_id": update["track_id"],
        "source_type": update["source"]["type"],
        "source_instance_id": update["source"]["instance_id"],
        "camera_id": update["camera_id"],
        "local_track_id": update["subject"]["local_track_id"],
        "subject_type": update["subject"]["type"],
        "status": status,
        "end_reason": end_reason,
        "source_lifecycle": lifecycle,
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
        "ended_at": ended_at,
        "first_received_at": (
            str(current["first_received_at"]) if current else received_text
        ),
        "last_received_at": received_text,
        "last_received_us": received_us,
        "last_phase": phase,
        "max_sequence": max(
            int(current["max_sequence"]) if current else 0, int(update["sequence"])
        ),
        "confidence": confidence,
        "geometry_json": geometry_json,
        "zones_json": zones_json,
        "attributes_json": _json(attributes),
        "media_json": _json(media),
        "quality_status": update["quality"]["status"],
    }


def _merge_media(
    current: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = list(current)
    seen = {(item["role"], item["path"]) for item in result}
    for item in incoming:
        key = (item["role"], item["path"])
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _latest_timestamp(current: str | None, incoming: str) -> str:
    if current is None:
        return incoming
    return max((current, incoming), key=lambda value: datetime.fromisoformat(value))


def _earliest_timestamp(current: str | None, incoming: str) -> str:
    if current is None:
        return incoming
    return min((current, incoming), key=lambda value: datetime.fromisoformat(value))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("tracking timestamp must include a timezone")
    return value.astimezone(timezone.utc)
