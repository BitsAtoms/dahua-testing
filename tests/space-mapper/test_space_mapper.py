from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAPPER_ROOT = REPOSITORY_ROOT / "services" / "space-mapper"
sys.path.insert(0, str(MAPPER_ROOT))

from space_mapper import (  # noqa: E402
    MapError,
    SpaceMapStore,
    default_map,
    discover_camera_ids,
    validate_map,
)


EXAMPLE = MAPPER_ROOT / "space-map.example.json"


class SpaceMapperTests(unittest.TestCase):
    def test_default_and_committed_example_are_valid(self) -> None:
        validate_map(default_map())
        validate_map(json.loads(EXAMPLE.read_text(encoding="utf-8")))

    def test_store_round_trip_is_atomic_and_preserves_mobile_pose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            document["cameras"][0]["fixed_pose"] = False
            store = SpaceMapStore(path)

            store.save(document)

            self.assertEqual(store.load(), document)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_rejects_unknown_space_reference(self) -> None:
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        document["cameras"][0]["space_id"] = "missing"

        with self.assertRaisesRegex(MapError, "unknown space"):
            validate_map(document)

    def test_rejects_duplicate_camera_and_invalid_transition_window(self) -> None:
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        document["cameras"].append(copy.deepcopy(document["cameras"][0]))
        with self.assertRaisesRegex(MapError, "duplicate camera"):
            validate_map(document)

        document["cameras"].pop()
        document["transitions"][0]["min_seconds"] = 31
        with self.assertRaisesRegex(MapError, "must not exceed"):
            validate_map(document)

        document["transitions"][0]["min_seconds"] = 1
        document["transitions"][0]["overlap_tolerance_seconds"] = 61
        with self.assertRaisesRegex(MapError, "between 0 and 60"):
            validate_map(document)

    def test_discovers_distinct_camera_ids_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receiver.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE track_updates (camera_id TEXT NOT NULL)")
            connection.executemany(
                "INSERT INTO track_updates(camera_id) VALUES (?)",
                [("cam_b",), ("cam_a",), ("cam_b",)],
            )
            connection.commit()
            connection.close()

            self.assertEqual(discover_camera_ids(database), ["cam_a", "cam_b"])


if __name__ == "__main__":
    unittest.main()
