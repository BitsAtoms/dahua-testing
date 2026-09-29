"""End-to-end join of live Dahua tracks and HumanTrait photos.

Exercises the collector pieces (live lane, correlator, projection) and the
real tracking-engine store together, without a camera or broker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "experiments" / "dahua-netsdk" / "collector"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "track-receiver"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "tracking-engine"))

from dahua_collector import DahuaEventCorrelator, observation_to_track_update  # noqa: E402
from dahua_collector.live_lane import DahuaLiveLane, LiveTarget  # noqa: E402
from track_receiver import validate_track_update  # noqa: E402
from tracking_engine import TrackingStore  # noqa: E402


START = 1_790_676_000.0  # 2026-09-29T10:00:00Z (PC time)
CAMERA_CLOCK = 1_788_000_000  # a skewed camera RealUTC, about 31 days behind


def cgi_body(object_id: int = 1520) -> dict:
    return {
        "action": "Start",
        "object_type": "Human",
        "group_id": 333,
        "object_id": object_id,
        "relative_id": 1_000_000 + object_id,
        "belong_id": object_id,
        "event_id": 11112,
        "event_uuid": "cgi-body-uuid",
        "timestamp": CAMERA_CLOCK,
        "_received_at": "2026-09-29T10:00:02.400000+00:00",
        "bounding_box": [2000, 1000, 4000, 8000],
        "attributes": {},
    }


def netsdk_media() -> dict:
    return {
        "group_id": 333,
        "timestamp": "20260829T052000-000",
        "snapshots": [
            {"type": "body", "file": "body.jpg"},
            {"type": "face", "file": "face.jpg"},
        ],
        "_python_received_at": "2026-09-29T10:00:02.500000+00:00",
    }


class LiveJoinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lane = DahuaLiveLane("dahua_212", "S", clock=lambda: START + 3)
        self.correlator = DahuaEventCorrelator()
        self.messages: list[dict] = []
        for step in range(21):  # 0.0 .. 2.0 s at 10 Hz, moving right
            box = (1000 + 100 * step, 1000, 3000 + 100 * step, 8000)
            self.messages += self.lane.ingest(LiveTarget(1520, box, START + step / 10, 500 + step))

    def snapshots(self) -> list[dict]:
        observations = self.correlator.ingest_netsdk("dahua_212", netsdk_media())
        observations += self.correlator.ingest_cgi("dahua_212", cgi_body())
        updates = []
        for observation in observations:
            observation["timing"]["collector_published_at"] = "2026-09-29T10:00:02.600000+00:00"
            local = observation["subject"]["local_track_id"]
            updates.append(observation_to_track_update(observation, self.lane.track_for_object(int(local))))
        return updates

    def test_humantrait_joins_the_live_track(self) -> None:
        self.messages += self.lane.finalize(1520, START + 2.4)
        snapshots = self.snapshots()
        live_track_id = self.messages[0]["track_id"]
        self.assertTrue(snapshots)
        for snapshot in snapshots:
            validate_track_update(snapshot)
            self.assertEqual(snapshot["track_id"], live_track_id)
            self.assertEqual(snapshot["phase"], "snapshot")
            self.assertEqual(snapshot["quality"]["source_lifecycle"], "live")
            self.assertTrue(snapshot["quality"]["joined_live_track"])
            self.assertIsNone(snapshot["geometry"])
            # PC time of the first sighting, never the skewed camera clock.
            self.assertEqual(snapshot["observed_at"], "2026-09-29T10:00:00+00:00")
            self.assertTrue(snapshot["quality"]["source_observed_at"].startswith("2026-08-29"))

    def test_tracking_engine_keeps_one_ended_track_with_photos_and_live_position(self) -> None:
        self.messages += self.lane.finalize(1520, START + 2.4)
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(Path(directory) / "tracking.sqlite3")
            try:
                received = datetime.fromtimestamp(START + 3, timezone.utc)
                for message in self.messages + self.snapshots():
                    store.project(message, received)
                tracks = store.list_tracks()
                self.assertEqual(len(tracks), 1)
                (track,) = tracks
                self.assertEqual(track["status"], "ended")
                self.assertEqual(track["end_reason"], "source_end")
                self.assertEqual({item["role"] for item in track["media"]}, {"body", "face"})
                # Last live box (step 20), not the HumanTrait best-frame box.
                self.assertEqual(track["geometry"]["box"]["x_min"], round(3000 / 8192, 6))
                self.assertEqual(track["first_observed_at"], "2026-09-29T10:00:00+00:00")
                self.assertEqual(track["last_observed_at"], "2026-09-29T10:00:02+00:00")
            finally:
                store.close()

    def test_without_a_live_track_the_snapshot_stays_finalized_only(self) -> None:
        lane = DahuaLiveLane("dahua_212", "S")
        observation = self.correlator.ingest_cgi("dahua_212", cgi_body(object_id=77))[0]
        observation["timing"]["collector_published_at"] = "2026-09-29T10:00:02.600000+00:00"
        update = observation_to_track_update(observation, lane.track_for_object(77))
        validate_track_update(update)
        self.assertEqual(update["quality"]["source_lifecycle"], "finalized_only")
        self.assertEqual(update["track_id"], observation["observation_id"])


if __name__ == "__main__":
    unittest.main()
