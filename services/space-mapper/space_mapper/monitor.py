"""Read-only operational view over local tracks and visual handoff evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def monitor_snapshot(
    tracking_database: Path,
    evidence_database: Path,
    camera_spaces: dict[str, str | None],
    *,
    recent_seconds: float = 120,
    now: datetime | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_us = round(
        (current - timedelta(seconds=recent_seconds)).timestamp() * 1_000_000
    )
    if not tracking_database.is_file():
        return _empty(current, "tracking_database_missing")
    connection = sqlite3.connect(
        f"file:{tracking_database.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        candidates = [
            dict(row)
            for row in connection.execute(
                """
                SELECT candidate_id, origin_track_id, destination_track_id,
                       origin_space_id, destination_space_id, gap_seconds,
                       score AS timing_score, observed_at, observed_us
                FROM handoff_candidates
                WHERE observed_us >= ?
                ORDER BY observed_us DESC, score DESC
                LIMIT ?
                """,
                (cutoff_us, limit),
            )
        ]
        referenced = {
            track_id
            for candidate in candidates
            for track_id in (
                candidate["origin_track_id"],
                candidate["destination_track_id"],
            )
        }
        recent_ids = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT track_id FROM local_tracks
                WHERE status = 'active' OR last_received_us >= ?
                ORDER BY last_received_us DESC
                LIMIT ?
                """,
                (cutoff_us, limit),
            )
        }
        track_ids = recent_ids | referenced
        tracks = _load_tracks(connection, track_ids, camera_spaces, current)
    except sqlite3.Error:
        return _empty(current, "tracking_database_unavailable")
    finally:
        connection.close()

    projected_candidates = _project_candidates(candidates, evidence_database)
    return {
        "schema_version": "space_monitor.v1",
        "generated_at": current.isoformat(),
        "recent_seconds": recent_seconds,
        "identity_assignment_enabled": False,
        "status": "ok",
        "tracks": tracks,
        "candidates": projected_candidates,
    }


def validation_snapshot(
    tracking_database: Path,
    evidence_database: Path,
    camera_spaces: dict[str, str | None],
    started_us: int,
    ended_us: int,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """Freeze references and scores received during one controlled test."""
    if not tracking_database.is_file():
        raise FileNotFoundError(f"tracking database does not exist: {tracking_database}")
    connection = sqlite3.connect(
        f"file:{tracking_database.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        candidates = [
            dict(row)
            for row in connection.execute(
                """
                SELECT candidate_id, origin_track_id, destination_track_id,
                       origin_space_id, destination_space_id, gap_seconds,
                       score AS timing_score, observed_at, observed_us
                FROM handoff_candidates
                WHERE observed_us BETWEEN ? AND ?
                ORDER BY observed_us, score DESC
                LIMIT ?
                """,
                (started_us, ended_us, limit),
            )
        ]
        referenced = {
            track_id
            for candidate in candidates
            for track_id in (
                candidate["origin_track_id"],
                candidate["destination_track_id"],
            )
        }
        received = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT track_id FROM local_tracks
                WHERE last_received_us BETWEEN ? AND ?
                ORDER BY last_received_us, track_id
                LIMIT ?
                """,
                (started_us, ended_us, limit),
            )
        }
        ended = datetime.fromtimestamp(ended_us / 1_000_000, tz=timezone.utc)
        tracks = _load_tracks(
            connection, received | referenced, camera_spaces, ended
        )
    finally:
        connection.close()
    return {
        "schema_version": "validation_evidence.v1",
        "started_us": started_us,
        "ended_us": ended_us,
        "tracks": tracks,
        "candidates": _project_candidates(candidates, evidence_database),
    }


def _load_tracks(
    connection: sqlite3.Connection,
    track_ids: set[str],
    camera_spaces: dict[str, str | None],
    now: datetime,
) -> list[dict[str, Any]]:
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    rows = connection.execute(
        f"""
        SELECT track_id, source_type, camera_id, local_track_id, subject_type,
               status, first_observed_at, last_observed_at, ended_at,
               last_received_at, last_received_us, last_phase, media_json
        FROM local_tracks
        WHERE track_id IN ({placeholders})
        ORDER BY last_received_us DESC, track_id
        """,
        tuple(sorted(track_ids)),
    )
    result = []
    for row in rows:
        item = dict(row)
        received = datetime.fromtimestamp(
            int(item["last_received_us"]) / 1_000_000, tz=timezone.utc
        )
        media = json.loads(item.pop("media_json"))
        item["space_id"] = camera_spaces.get(str(item["camera_id"]))
        item["age_seconds"] = round(max(0, (now - received).total_seconds()), 3)
        item["media"] = media
        result.append(item)
    return result


def _load_evidence(path: Path, candidate_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not candidate_ids or not path.is_file():
        return {}
    placeholders = ",".join("?" for _ in candidate_ids)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT candidate_id, evidence_json
            FROM candidate_evidence
            WHERE candidate_id IN ({placeholders})
            """,
            tuple(sorted(candidate_ids)),
        )
        return {str(row[0]): json.loads(row[1]) for row in rows}
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def _project_candidates(
    candidates: list[dict[str, Any]], evidence_database: Path
) -> list[dict[str, Any]]:
    evidence = _load_evidence(
        evidence_database, {str(item["candidate_id"]) for item in candidates}
    )
    result = []
    for candidate in candidates:
        visual = evidence.get(str(candidate["candidate_id"]))
        result.append(
            {
                **candidate,
                "visual_ranking": (
                    visual.get("ranking_score") if visual is not None else None
                ),
                "visual_coverage": (
                    visual.get("visual_coverage") if visual is not None else None
                ),
                "available_modalities": (
                    visual.get("available_modalities", [])
                    if visual is not None
                    else []
                ),
                "signals": visual.get("signals", {}) if visual is not None else {},
                "identity_decision": None,
            }
        )
    return result


def _empty(now: datetime, status: str) -> dict[str, Any]:
    return {
        "schema_version": "space_monitor.v1",
        "generated_at": now.isoformat(),
        "recent_seconds": 120,
        "identity_assignment_enabled": False,
        "status": status,
        "tracks": [],
        "candidates": [],
    }
