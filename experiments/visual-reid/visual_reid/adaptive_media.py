"""Seven-day catalog for selected adaptive crops; embeddings stay in memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .adaptive_capture import EvidenceObservation


RETENTION_DAYS = 7
MAX_ASSETS_PER_TRACK_MODALITY = 5
ADAPTIVE_BODY_VALIDITY_VERSION = "adaptive-body-observed-time.v2-live-only"


class AdaptiveMediaStore:
    def __init__(self, database: Path, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS adaptive_media (
                asset_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                modality TEXT NOT NULL,
                observed_us INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                created_us INTEGER NOT NULL,
                quality_score REAL NOT NULL,
                quality_json TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS adaptive_media_track
                ON adaptive_media(track_id, modality, observed_us);
            CREATE INDEX IF NOT EXISTS adaptive_media_retention
                ON adaptive_media(created_us);
            """
        )
        self._connection.commit()

    def enforce_limits(
        self, max_per_track: int = MAX_ASSETS_PER_TRACK_MODALITY
    ) -> int:
        """Keep only the strongest bounded set for each track and modality."""
        if max_per_track <= 0:
            raise ValueError("max_per_track must be positive")
        groups = self._connection.execute(
            """
            SELECT track_id, modality
            FROM adaptive_media
            GROUP BY track_id, modality
            HAVING COUNT(*) > ?
            """,
            (max_per_track,),
        ).fetchall()
        removed = 0
        for track_id, modality in groups:
            excess = self._connection.execute(
                """
                SELECT asset_id, path
                FROM adaptive_media
                WHERE track_id=? AND modality=?
                ORDER BY quality_score DESC, observed_us DESC, asset_id ASC
                LIMIT -1 OFFSET ?
                """,
                (track_id, modality, max_per_track),
            ).fetchall()
            removable: list[str] = []
            for asset_id, raw_path in excess:
                path = Path(raw_path).resolve()
                if self.root not in path.parents:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                removable.append(str(asset_id))
            if removable:
                placeholders = ",".join("?" for _ in removable)
                self._connection.execute(
                    f"DELETE FROM adaptive_media WHERE asset_id IN ({placeholders})",
                    removable,
                )
                removed += len(removable)
        if removed:
            self._connection.commit()
        return removed

    def save_observations(
        self, observations: tuple[EvidenceObservation, ...]
    ) -> int:
        saved = 0
        now = datetime.now(timezone.utc)
        created_us = round(now.timestamp() * 1_000_000)
        for item in observations:
            payload = item.payload if isinstance(item.payload, dict) else {}
            jpeg = payload.get("crop_jpeg")
            if payload.get("kind") != "buffer_frame" or not isinstance(jpeg, bytes):
                continue
            asset_id = hashlib.sha256(item.observation_id.encode("utf-8")).hexdigest()[:24]
            camera = re.sub(r"[^A-Za-z0-9_-]", "_", item.camera_id)[:64]
            directory = self.root / camera
            directory.mkdir(parents=True, exist_ok=True)
            path = (directory / f"{asset_id}_{item.quality.modality}.jpg").resolve()
            if self.root not in path.parents:
                raise ValueError("adaptive media path escaped its root")
            cursor = self._connection.execute(
                "SELECT 1 FROM adaptive_media WHERE asset_id=?", (asset_id,)
            )
            if cursor.fetchone() is not None:
                continue
            temporary = path.with_suffix(".jpg.tmp")
            temporary.write_bytes(jpeg)
            temporary.replace(path)
            quality = {
                "modality": item.quality.modality,
                "score": item.quality.score,
                "strong": item.quality.strong,
                "usable_for_embedding": item.quality.usable_for_embedding,
                "reasons": list(item.quality.reasons),
            }
            confidence = payload.get("detector_confidence")
            if isinstance(confidence, (int, float)):
                quality["detector_confidence"] = round(float(confidence), 6)
            box = payload.get("detector_box")
            if (
                isinstance(box, list)
                and len(box) == 4
                and all(isinstance(value, int) for value in box)
            ):
                quality["detector_box"] = box
            validity_version = payload.get("face_validity_version")
            if item.quality.modality == "face" and isinstance(
                validity_version, str
            ):
                quality["face_validity_version"] = validity_version
            body_validity_version = payload.get("body_validity_version")
            if item.quality.modality == "body" and isinstance(
                body_validity_version, str
            ):
                quality["body_validity_version"] = body_validity_version
            alignment_delta_us = payload.get("alignment_delta_us")
            if item.quality.modality == "body" and isinstance(
                alignment_delta_us, int
            ):
                quality["alignment_delta_us"] = alignment_delta_us
            self._connection.execute(
                """
                INSERT INTO adaptive_media (
                    asset_id, observation_id, track_id, camera_id, modality,
                    observed_us, created_at, created_us, quality_score,
                    quality_json, path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    item.observation_id,
                    item.track_id,
                    item.camera_id,
                    item.quality.modality,
                    item.observed_us,
                    now.isoformat(),
                    created_us,
                    item.quality.score,
                    json.dumps(quality, separators=(",", ":")),
                    str(path),
                ),
            )
            saved += 1
        self._connection.commit()
        self.enforce_limits()
        return saved

    def cleanup(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff_us = round(
            (current - timedelta(days=RETENTION_DAYS)).timestamp() * 1_000_000
        )
        rows = self._connection.execute(
            "SELECT asset_id, path FROM adaptive_media WHERE created_us < ?",
            (cutoff_us,),
        ).fetchall()
        removed: list[str] = []
        for asset_id, raw_path in rows:
            path = Path(raw_path).resolve()
            if self.root not in path.parents:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            removed.append(asset_id)
        if removed:
            placeholders = ",".join("?" for _ in removed)
            self._connection.execute(
                f"DELETE FROM adaptive_media WHERE asset_id IN ({placeholders})",
                removed,
            )
            self._connection.commit()
        return len(removed)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> AdaptiveMediaStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def adaptive_media_revisions(
    database: Path, track_ids: list[str]
) -> dict[str, int]:
    if not database.is_file() or not track_ids:
        return {}
    requested = list(dict.fromkeys(track_ids))
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        placeholders = ",".join("?" for _ in requested)
        rows = connection.execute(
            f"""
            SELECT track_id, MAX(created_us)
            FROM adaptive_media
            WHERE track_id IN ({placeholders})
            GROUP BY track_id
            """,
            requested,
        )
        return {str(track): int(revision) for track, revision in rows}
    finally:
        connection.close()
