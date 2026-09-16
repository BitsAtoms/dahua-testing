from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKING_ROOT = REPOSITORY_ROOT / "services" / "tracking-engine"
sys.path.insert(0, str(TRACKING_ROOT))

from tracking_engine import TrackingStore  # noqa: E402


NOW = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)


class TrackingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = TrackingStore(Path(self.temporary.name) / "tracking.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_live_lifecycle_ends_and_snapshot_enriches_without_reopening(self) -> None:
        self.store.project(update("new", 1), NOW)
        self.store.project(update("update", 2, x=0.2), NOW + timedelta(seconds=1))
        self.store.project(update("end", 3, x=0.3), NOW + timedelta(seconds=2))
        self.store.project(
            update("snapshot", 2, x=0.3, media=[media("snapshot.jpg")]),
            NOW + timedelta(seconds=3),
        )

        track = self.store.get_track("frigate:cam-a:track-1")
        self.assertIsNotNone(track)
        self.assertEqual(track["status"], "ended")
        self.assertEqual(track["last_phase"], "snapshot")
        self.assertEqual(track["max_sequence"], 3)
        self.assertEqual(track["geometry"]["center"]["x"], 0.4)
        self.assertEqual(track["media"], [media("snapshot.jpg")])

    def test_duplicate_message_is_not_applied_twice(self) -> None:
        message = update("new", 1)
        first = self.store.project(message, NOW)
        second = self.store.project(message, NOW + timedelta(seconds=1))

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(self.store.count(), 1)

    def test_late_update_does_not_reopen_ended_track(self) -> None:
        self.store.project(update("end", 3, x=0.3), NOW)
        result = self.store.project(
            update("update", 2, x=0.1), NOW + timedelta(seconds=1)
        )

        self.assertEqual(result.status, "ended")
        track = self.store.get_track(result.track_id)
        self.assertEqual(track["status"], "ended")
        self.assertEqual(track["end_reason"], "source_end")
        self.assertEqual(track["geometry"]["center"]["x"], 0.4)
        self.assertEqual(
            track["first_observed_at"], (NOW + timedelta(seconds=2)).isoformat()
        )

    def test_stale_track_expires_and_new_activity_reopens_it(self) -> None:
        self.store.project(update("new", 1), NOW)
        expired = self.store.expire_stale(NOW + timedelta(seconds=121))

        self.assertEqual(expired, 1)
        self.assertEqual(
            self.store.get_track("frigate:cam-a:track-1")["end_reason"], "timeout"
        )

        result = self.store.project(
            update("update", 2), NOW + timedelta(seconds=122)
        )
        track = self.store.get_track(result.track_id)
        self.assertEqual(track["status"], "active")
        self.assertIsNone(track["end_reason"])
        self.assertIsNone(track["ended_at"])

    def test_finalized_only_snapshot_creates_ended_dahua_track(self) -> None:
        message = update("snapshot", 1, source="dahua", lifecycle="finalized_only")
        message["track_id"] = "dahua:cam-a:144"
        message["published_at"] = (NOW + timedelta(seconds=20)).isoformat()
        result = self.store.project(message, NOW)

        self.assertEqual(result.status, "ended")
        track = self.store.get_track(result.track_id)
        self.assertEqual(track["source_type"], "dahua")
        self.assertEqual(track["end_reason"], "source_finalized")
        self.assertEqual(track["ended_at"], message["published_at"])

    def test_cleanup_uses_seven_day_policy_for_all_derived_data(self) -> None:
        self.store.project(update("end", 1), NOW - timedelta(days=8))
        removed = self.store.cleanup(NOW)

        self.assertEqual(removed, 2)
        self.assertEqual(self.store.count(), 0)

    def test_projection_version_resets_only_rebuildable_state_once(self) -> None:
        self.store.project(update("new", 1), NOW)

        self.assertTrue(self.store.ensure_projection_version())
        self.assertEqual(self.store.count(), 0)
        self.assertFalse(self.store.ensure_projection_version())


def update(
    phase: str,
    sequence: int,
    *,
    x: float = 0.1,
    source: str = "frigate",
    lifecycle: str = "live",
    media: list[dict[str, str]] | None = None,
) -> dict:
    observed = (NOW + timedelta(seconds=sequence)).isoformat()
    return {
        "schema_version": "track_update.v1",
        "message_id": f"message-{phase}-{sequence}",
        "track_id": "frigate:cam-a:track-1",
        "source": {"type": source, "instance_id": "test"},
        "camera_id": "cam-a",
        "phase": phase,
        "sequence": sequence,
        "observed_at": observed,
        "published_at": observed,
        "subject": {
            "type": "person",
            "local_track_id": "track-1",
            "confidence": 0.8,
        },
        "geometry": {
            "coordinate_space": "normalized_0_1",
            "box": {
                "x_min": x,
                "y_min": 0.2,
                "x_max": x + 0.2,
                "y_max": 0.8,
            },
            "center": {"x": x + 0.1, "y": 0.5},
        },
        "zones": {"current": [], "entered": []},
        "attributes": {"stationary": False},
        "media": media or [],
        "quality": {
            "status": "complete",
            "source_lifecycle": lifecycle,
            "issues": [],
        },
        "source_ref": {
            "event_id": "event-1",
            "message_id": f"source-{phase}-{sequence}",
        },
    }


def media(path: str) -> dict[str, str]:
    return {"role": "snapshot", "content_type": "image/jpeg", "path": path}


if __name__ == "__main__":
    unittest.main()
