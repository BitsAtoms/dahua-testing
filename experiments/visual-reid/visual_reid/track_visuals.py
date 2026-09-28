"""Resolve the best retained body input for normalized tracks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from .face_detection import ADAPTIVE_FACE_VALIDITY_VERSION
from .adaptive_media import ADAPTIVE_BODY_VALIDITY_VERSION


@dataclass(frozen=True)
class TrackVisual:
    track_id: str
    source_type: str
    camera_id: str
    role: str
    path: Path
    normalized_box: dict[str, float] | None
    observed_us: int = 0
    quality_weight: float = 1.0


def load_track_visuals(
    receiver_database: Path,
    track_ids: list[str],
    preferred_roles: tuple[str, ...] = ("body", "snapshot"),
    adaptive_media_database: Path | None = None,
) -> dict[str, TrackVisual]:
    """Load one body-capable visual per requested track from the durable log."""
    sets = load_track_visual_sets(
        receiver_database,
        track_ids,
        preferred_roles,
        adaptive_media_database=adaptive_media_database,
    )
    return {track_id: visuals[-1] for track_id, visuals in sets.items() if visuals}


def load_track_visual_sets(
    receiver_database: Path,
    track_ids: list[str],
    preferred_roles: tuple[str, ...] = ("body", "snapshot"),
    *,
    adaptive_media_database: Path | None = None,
) -> dict[str, list[TrackVisual]]:
    """Load all distinct preferred visuals, plus selected adaptive body crops."""
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
            SELECT track_id, receiver_received_us, payload_json
            FROM track_updates
            WHERE track_id IN ({placeholders})
            ORDER BY receiver_received_us, rowid
            """,
            requested,
        )
        merged: dict[str, dict[str, Any]] = {}
        for track_id, received_us, payload_json in rows:
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
                    role = str(media.get("role", ""))
                    entries = state["media"].setdefault(role, [])
                    if all(existing[0] != path for existing in entries):
                        entries.append((path, int(received_us)))
    finally:
        connection.close()

    result: dict[str, list[TrackVisual]] = {}
    for track_id, state in merged.items():
        role = next(
            (item for item in preferred_roles if item in state["media"]), None
        )
        if role is None:
            continue
        geometry = state["geometry"]
        box = geometry.get("box") if geometry and role == "snapshot" else None
        result[track_id] = [
            TrackVisual(
                track_id=track_id,
                source_type=state["source_type"],
                camera_id=state["camera_id"],
                role=role,
                path=path,
                normalized_box=box,
                observed_us=observed_us,
            )
            for path, observed_us in state["media"].get(role, [])
        ]
    adaptive_modalities = set()
    if any(role in {"body", "snapshot"} for role in preferred_roles):
        adaptive_modalities.add("body")
    if "face" in preferred_roles:
        adaptive_modalities.add("face")
    if (
        adaptive_media_database is not None
        and adaptive_media_database.is_file()
        and adaptive_modalities
    ):
        connection = sqlite3.connect(
            f"file:{adaptive_media_database.resolve().as_posix()}?mode=ro", uri=True
        )
        try:
            placeholders = ",".join("?" for _ in requested)
            rows = connection.execute(
                f"""
                SELECT track_id, camera_id, modality, observed_us,
                       quality_score, quality_json, path
                FROM adaptive_media
                WHERE track_id IN ({placeholders})
                ORDER BY observed_us, asset_id
                """,
                requested,
            )
            for (
                track_id,
                camera_id,
                modality,
                observed_us,
                quality_score,
                quality_json,
                raw_path,
            ) in rows:
                path = Path(raw_path)
                if not path.is_file():
                    continue
                if modality not in adaptive_modalities:
                    continue
                if modality == "face":
                    try:
                        quality_metadata = json.loads(quality_json or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        quality_metadata.get("face_validity_version")
                        != ADAPTIVE_FACE_VALIDITY_VERSION
                    ):
                        continue
                elif modality == "body":
                    try:
                        quality_metadata = json.loads(quality_json or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        quality_metadata.get("body_validity_version")
                        != ADAPTIVE_BODY_VALIDITY_VERSION
                    ):
                        continue
                result.setdefault(str(track_id), []).append(
                    TrackVisual(
                        track_id=str(track_id),
                        source_type="adaptive_rtsp",
                        camera_id=str(camera_id),
                        role=f"adaptive_{modality}",
                        path=path,
                        normalized_box=None,
                        observed_us=int(observed_us),
                        quality_weight=float(quality_score),
                    )
                )
        finally:
            connection.close()
    for visuals in result.values():
        visuals.sort(key=lambda item: (item.observed_us, str(item.path)))
    return result
