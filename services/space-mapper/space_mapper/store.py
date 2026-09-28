"""Atomic persistence and camera discovery for the local space mapper."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .model import default_map, validate_map


class SpaceMapStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_map()
        document = json.loads(self.path.read_text(encoding="utf-8-sig"))
        validate_map(document)
        return document

    def save(self, document: dict[str, Any]) -> None:
        validate_map(document)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def discover_camera_ids(receiver_database: Path) -> list[str]:
    if not receiver_database.is_file():
        return []
    connection = sqlite3.connect(
        f"file:{receiver_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            "SELECT DISTINCT camera_id FROM track_updates ORDER BY camera_id"
        )
        return [str(row[0]) for row in rows]
    finally:
        connection.close()
