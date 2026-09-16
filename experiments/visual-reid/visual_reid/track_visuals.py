"""Resolve the best retained body input for normalized tracks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True)
class TrackVisual:
    track_id: str
    source_type: str
    camera_id: str
    role: str
    path: Path
    normalized_box: dict[str, float] | None


def load_track_visuals(
    receiver_database: Path,
    track_ids: list[str],
    preferred_roles: tuple[str, ...] = ("body", "snapshot"),
) -> dict[str, TrackVisual]:
    """Load one body-capable visual per requested track from the durable log."""
    if not receiver_database.is_file():
        raise FileNotFoundError(f"receiver database does not exist: {receiver_database}")
    requested = list(dict.fromkeys(track_ids))
    if not requested:
        return {}
    connection = sqlite3.connect(
        f"file:{receiver_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        placeholders = ",".join("?" for _ in requested)
        rows = connection.execute(
            f"""
            SELECT track_id, payload_json
            FROM track_updates
            WHERE track_id IN ({placeholders})
            ORDER BY receiver_received_us, rowid
            """,
            requested,
        )
        merged: dict[str, dict[str, Any]] = {}
        for track_id, payload_json in rows:
            update = json.loads(payload_json)
            state = merged.setdefault(
                track_id,
                {
                    "source_type": update["source"]["type"],
                    "camera_id": update["camera_id"],
                    "geometry": None,
                    "media": {},
                },
            )
            if update.get("geometry") is not None:
                state["geometry"] = update["geometry"]
            for media in update.get("media") or []:
                path = Path(str(media.get("path", "")))
                if path.is_file():
                    state["media"][str(media.get("role", ""))] = path
    finally:
        connection.close()

    result: dict[str, TrackVisual] = {}
    for track_id, state in merged.items():
        role = next(
            (item for item in preferred_roles if item in state["media"]), None
        )
        if role is None:
            continue
        path = state["media"].get(role)
        if path is None:
            continue
        geometry = state["geometry"]
        box = geometry.get("box") if geometry and role == "snapshot" else None
        result[track_id] = TrackVisual(
            track_id=track_id,
            source_type=state["source_type"],
            camera_id=state["camera_id"],
            role=role,
            path=path,
            normalized_box=box,
        )
    return result
