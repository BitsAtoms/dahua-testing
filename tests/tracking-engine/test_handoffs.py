from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKING_ROOT = REPOSITORY_ROOT / "services" / "tracking-engine"
sys.path.insert(0, str(TRACKING_ROOT))

from tracking_engine import HandoffEngine, SpaceTopology, TrackingStore  # noqa: E402


NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


class HandoffEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.map_path = root / "space-map.json"
        self.store = TrackingStore(root / "tracking.sqlite3")
        self.write_map(max_seconds=10)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_builds_explainable_candidate_inside_directed_time_window(self) -> None:
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message("track-b", "cam-b", "new", NOW + timedelta(seconds=4)),
            NOW + timedelta(seconds=4),
        )
        engine = HandoffEngine(self.map_path, self.store)

        sync = engine.sync_topology()

        self.assertTrue(sync.enabled)
        self.assertEqual(sync.candidates, 1)
        candidate = self.store.list_handoff_candidates()[0]
        self.assertEqual(candidate["origin_track_id"], "track-a")
        self.assertEqual(candidate["destination_track_id"], "track-b")
        self.assertEqual(candidate["gap_seconds"], 4)
        self.assertEqual(candidate["score"], 0.75)
        self.assertTrue(candidate["evidence"]["ranking_not_identity_probability"])
        self.assertEqual(engine.evaluate_destination("track-b"), 0)

    def test_rejects_reverse_direction_and_outside_window(self) -> None:
        self.store.project(message("reverse-a", "cam-b", "end", NOW), NOW)
        self.store.project(
            message("reverse-b", "cam-a", "new", NOW + timedelta(seconds=4)),
            NOW + timedelta(seconds=4),
        )
        late_origin = NOW + timedelta(seconds=20)
        self.store.project(
            message("late-a", "cam-a", "end", late_origin), late_origin
        )
        self.store.project(
            message("late-b", "cam-b", "new", late_origin + timedelta(seconds=11)),
            late_origin + timedelta(seconds=11),
        )

        sync = HandoffEngine(self.map_path, self.store).sync_topology()

        self.assertEqual(sync.candidates, 0, self.store.list_handoff_candidates())

    def test_accepts_small_cross_camera_source_overlap(self) -> None:
        self.write_map(min_seconds=0, max_seconds=10)
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message(
                "track-b",
                "cam-b",
                "new",
                NOW - timedelta(milliseconds=400),
            ),
            NOW,
        )

        sync = HandoffEngine(self.map_path, self.store).sync_topology()

        self.assertEqual(sync.candidates, 1, self.store.list_handoff_candidates())
        candidate = self.store.list_handoff_candidates()[0]
        self.assertAlmostEqual(candidate["gap_seconds"], -0.4)
        self.assertEqual(candidate["score"], 1.0)
        self.assertEqual(
            candidate["evidence"]["timing"]["ranking_gap_seconds"], 0
        )
        self.assertEqual(
            candidate["evidence"]["timing"][
                "source_overlap_tolerance_seconds"
            ],
            2.0,
        )

    def test_rejects_cross_camera_overlap_beyond_tolerance(self) -> None:
        self.write_map(min_seconds=0, max_seconds=10)
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message(
                "track-b",
                "cam-b",
                "new",
                NOW - timedelta(seconds=3),
            ),
            NOW,
        )

        sync = HandoffEngine(self.map_path, self.store).sync_topology()

        self.assertEqual(sync.candidates, 0)

    def test_uses_transition_specific_overlap_tolerance(self) -> None:
        self.write_map(min_seconds=0, max_seconds=10, overlap_seconds=5)
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message(
                "track-b",
                "cam-b",
                "new",
                NOW - timedelta(seconds=4.5),
            ),
            NOW,
        )

        sync = HandoffEngine(self.map_path, self.store).sync_topology()

        self.assertEqual(sync.candidates, 1)
        candidate = self.store.list_handoff_candidates()[0]
        self.assertEqual(candidate["gap_seconds"], -4.5)
        self.assertEqual(
            candidate["evidence"]["timing"][
                "source_overlap_tolerance_seconds"
            ],
            5,
        )

    def test_map_change_rebuilds_and_removes_invalid_candidates(self) -> None:
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message("track-b", "cam-b", "new", NOW + timedelta(seconds=4)),
            NOW + timedelta(seconds=4),
        )
        engine = HandoffEngine(self.map_path, self.store)
        self.assertEqual(engine.sync_topology().candidates, 1)

        self.write_map(max_seconds=3)
        changed = engine.sync_topology()

        self.assertTrue(changed.changed)
        self.assertEqual(changed.candidates, 0)
        self.assertEqual(self.store.handoff_candidate_count(), 0)

    def test_restart_reuses_persisted_projection_for_unchanged_map(self) -> None:
        self.store.project(message("track-a", "cam-a", "end", NOW), NOW)
        self.store.project(
            message("track-b", "cam-b", "new", NOW + timedelta(seconds=4)),
            NOW + timedelta(seconds=4),
        )
        first = HandoffEngine(self.map_path, self.store).sync_topology()
        restarted = HandoffEngine(self.map_path, self.store).sync_topology()

        self.assertTrue(first.changed)
        self.assertFalse(restarted.changed)
        self.assertEqual(restarted.candidates, 1)

    def test_committed_example_loads_as_tracking_topology(self) -> None:
        example = (
            REPOSITORY_ROOT / "services" / "space-mapper" / "space-map.example.json"
        )
        topology = SpaceTopology.load(example)

        self.assertEqual(topology.space_for_camera("camera_entry"), "entry")
        self.assertEqual(len(topology.transitions_between("room", "entry")), 1)

    def write_map(
        self,
        *,
        min_seconds: int = 2,
        max_seconds: int,
        overlap_seconds: int | None = None,
    ) -> None:
        document = {
            "schema_version": "space_map.v1",
            "site": {"id": "test", "name": "Test"},
            "spaces": [
                {"id": "a", "name": "A", "polygon": []},
                {"id": "b", "name": "B", "polygon": []},
            ],
            "cameras": [
                {"camera_id": "cam-a", "space_id": "a", "fixed_pose": True},
                {"camera_id": "cam-b", "space_id": "b", "fixed_pose": False},
            ],
            "transitions": [
                {
                    "id": "a-b",
                    "from_space_id": "a",
                    "to_space_id": "b",
                    "bidirectional": False,
                    "min_seconds": min_seconds,
                    "max_seconds": max_seconds,
                }
            ],
        }
        if overlap_seconds is not None:
            document["transitions"][0][
                "overlap_tolerance_seconds"
            ] = overlap_seconds
        self.map_path.write_text(json.dumps(document), encoding="utf-8")


def message(
    track_id: str, camera_id: str, phase: str, observed_at: datetime
) -> dict:
    sequence = round(observed_at.timestamp() * 1_000_000)
    return {
        "schema_version": "track_update.v1",
        "message_id": f"{track_id}:{phase}:{sequence}",
        "track_id": track_id,
        "source": {"type": "frigate", "instance_id": "test"},
        "camera_id": camera_id,
        "phase": phase,
        "sequence": sequence,
        "observed_at": observed_at.isoformat(),
        "published_at": observed_at.isoformat(),
        "subject": {
            "type": "person",
            "local_track_id": track_id,
            "confidence": 0.8,
        },
        "geometry": None,
        "zones": {"current": [], "entered": []},
        "attributes": {},
        "media": [],
        "quality": {
            "status": "complete",
            "source_lifecycle": "live",
            "issues": [],
        },
        "source_ref": {"event_id": track_id, "message_id": track_id},
    }


if __name__ == "__main__":
    unittest.main()
