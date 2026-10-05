from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "tracking-engine"))

from batcomputer_ui.live_map import LiveMap, read_tracking  # noqa: E402
from batcomputer_ui.spaces import SpaceMapStore  # noqa: E402
from tracking_engine.store import TrackingStore  # noqa: E402


EXAMPLE = REPOSITORY_ROOT / "services" / "batcomputer-ui" / "space-map.example.json"
NOW = 1_790_685_000.0


def iso(offset: float) -> str:
    return datetime.fromtimestamp(NOW + offset, timezone.utc).isoformat()


class LiveMapTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = SpaceMapStore(root / "spaces" / "space-map.json")
        self.store.save(json.loads(EXAMPLE.read_text(encoding="utf-8")), None)
        self.database = root / "tracking.sqlite3"
        TrackingStore(self.database).close()
        self.map = LiveMap(self.store, lambda now: read_tracking(self.database, now),
                           clock=lambda: NOW, cache_seconds=0)

    def track(self, track_id: str, camera_id: str, start: float, end: float, *, active: bool = False,
              center=(0.5, 0.5)) -> None:
        geometry = json.dumps({"center": {"x": center[0], "y": center[1]}})
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO local_tracks (track_id, source_type, camera_id, subject_type, status,
                       source_lifecycle, first_observed_at, last_observed_at, ended_at, first_received_at,
                       last_received_at, last_received_us, last_phase, max_sequence, geometry_json,
                       first_geometry_json, zones_json, attributes_json, media_json, quality_status)
                   VALUES (?, 'test', ?, 'person', ?, 'live', ?, ?, ?, ?, ?, ?, 'update', 1, ?, ?, '[]', '{}',
                       '{}', 'ok')""",
                (track_id, camera_id, "active" if active else "ended", iso(start), iso(end),
                 None if active else iso(end), iso(start), iso(end), round((NOW + end) * 1e6),
                 geometry, geometry),
            )

    def link(self, origin: str, destination: str, score: float) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO handoff_candidates (candidate_id, origin_track_id, destination_track_id,
                       transition_id, origin_space_id, destination_space_id, gap_seconds, score,
                       observed_at, observed_us, created_at, evidence_json)
                   VALUES (?, ?, ?, 't', 'a', 'b', 4, ?, ?, ?, ?, '{}')""",
                (f"{origin}>{destination}", origin, destination, score, iso(-6),
                 round((NOW - 6) * 1e6), iso(-6)),
            )

    def test_a_person_crossing_the_door_moves_to_the_next_room(self) -> None:
        self.track("e1", "camera_entrada", -40, -10)
        self.track("o1", "camera_oficina", -6, 0, active=True)
        self.link("e1", "o1", 0.8)

        snapshot = self.map.snapshot()

        self.assertEqual((snapshot["state"], snapshot["total"]), ("ok", 1))
        self.assertEqual(snapshot["rooms"]["room_entrada"], {"count": 0, "people": []})
        self.assertEqual(snapshot["rooms"]["room_oficina"], {"count": 1, "people": [{"id": "o1", "seen": True}]})
        self.assertEqual(snapshot["moves"], [{
            "id": "o1", "from_id": "e1", "from_room": "room_entrada", "to_room": "room_oficina",
            "via": {"kind": "door", "id": "door_entrada_oficina"}, "age": 6.0,
        }])

    def test_a_person_no_longer_seen_is_still_counted_for_a_while(self) -> None:
        self.track("e1", "camera_entrada", -40, -10)

        room = self.map.snapshot()["rooms"]["room_entrada"]

        self.assertEqual(room, {"count": 1, "people": [{"id": "e1", "seen": False}]})

    def test_the_plan_is_sent_without_cameras(self) -> None:
        plan = self.map.snapshot()["plan"]

        self.assertEqual([floor["name"] for floor in plan["floors"]], ["Planta 0", "Planta 1"])
        self.assertNotIn("cameras", plan["floors"][0])
        self.assertEqual(plan["floor_links"][0]["kind"], "stairs")

    def test_without_tracking_data_rooms_are_empty(self) -> None:
        self.database.unlink()

        snapshot = self.map.snapshot()

        self.assertEqual((snapshot["state"], snapshot["total"]), ("no_data", 0))
        self.assertEqual(snapshot["rooms"]["room_taller"], {"count": 0, "people": []})

    def test_without_a_plan_there_is_nothing_to_draw(self) -> None:
        empty = LiveMap(SpaceMapStore(self.database.parent / "missing.json"),
                        lambda now: read_tracking(self.database, now), clock=lambda: NOW)

        self.assertEqual(empty.snapshot()["state"], "no_plan")


if __name__ == "__main__":
    unittest.main()
