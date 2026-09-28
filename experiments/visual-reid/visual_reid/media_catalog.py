"""Build a source-neutral catalog from retained track-update media."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator


SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


@dataclass(frozen=True)
class MediaAsset:
    track_id: str
    source_type: str
    camera_id: str
    role: str
    path: Path
    exists: bool
    byte_count: int | None
    width: int | None
    height: int | None

    @property
    def valid_jpeg(self) -> bool:
        return self.width is not None and self.height is not None


def iter_media_assets(receiver_database: Path) -> Iterator[MediaAsset]:
    """Yield each distinct media reference stored in the receiver log."""
    if not receiver_database.is_file():
        raise FileNotFoundError(f"receiver database does not exist: {receiver_database}")
    connection = sqlite3.connect(
        f"file:{receiver_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM track_updates
            WHERE phase = 'snapshot'
            ORDER BY receiver_received_us, rowid
            """
        )
        seen: set[tuple[str, str, str]] = set()
        for (payload_json,) in rows:
            update = json.loads(payload_json)
            for media in update.get("media") or []:
                identity = (
                    update["track_id"],
                    str(media.get("role", "")),
                    str(media.get("path", "")),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                yield _asset(update, media)
    finally:
        connection.close()


def summarize_assets(assets: Iterable[MediaAsset]) -> list[dict[str, Any]]:
    """Aggregate catalog health by provider, camera and semantic media role."""
    groups: dict[tuple[str, str, str], list[MediaAsset]] = defaultdict(list)
    for asset in assets:
        groups[(asset.source_type, asset.camera_id, asset.role)].append(asset)
    result: list[dict[str, Any]] = []
    for (source, camera, role), items in sorted(groups.items()):
        sizes = [item.byte_count for item in items if item.byte_count is not None]
        dimensions = sorted(
            {
                f"{item.width}x{item.height}"
                for item in items
                if item.valid_jpeg
            }
        )
        result.append(
            {
                "source": source,
                "camera": camera,
                "role": role,
                "assets": len(items),
                "present": sum(item.exists for item in items),
                "valid_jpeg": sum(item.valid_jpeg for item in items),
                "average_bytes": round(sum(sizes) / len(sizes)) if sizes else None,
                "dimensions": dimensions,
            }
        )
    return result


def jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    """Read JPEG dimensions without loading pixels or adding image dependencies."""
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"\xff\xd8":
                return None
            while True:
                prefix = stream.read(1)
                if not prefix:
                    return None
                if prefix != b"\xff":
                    continue
                marker_byte = stream.read(1)
                while marker_byte == b"\xff":
                    marker_byte = stream.read(1)
                if not marker_byte:
                    return None
                marker = marker_byte[0]
                if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    continue
                length_bytes = stream.read(2)
                if len(length_bytes) != 2:
                    return None
                length = int.from_bytes(length_bytes, "big")
                if length < 2:
                    return None
                if marker in SOF_MARKERS:
                    header = stream.read(5)
                    if len(header) != 5:
                        return None
                    height = int.from_bytes(header[1:3], "big")
                    width = int.from_bytes(header[3:5], "big")
                    return (width, height) if width and height else None
                stream.seek(length - 2, 1)
    except OSError:
        return None


def _asset(update: dict[str, Any], media: dict[str, Any]) -> MediaAsset:
    path = Path(str(media.get("path", "")))
    exists = path.is_file()
    dimensions = jpeg_dimensions(path) if exists else None
    return MediaAsset(
        track_id=str(update["track_id"]),
        source_type=str(update["source"]["type"]),
        camera_id=str(update["camera_id"]),
        role=str(media.get("role", "")),
        path=path,
        exists=exists,
        byte_count=path.stat().st_size if exists else None,
        width=dimensions[0] if dimensions else None,
        height=dimensions[1] if dimensions else None,
    )
