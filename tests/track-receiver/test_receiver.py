from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECEIVER_ROOT = REPOSITORY_ROOT / "services" / "track-receiver"
sys.path.insert(0, str(RECEIVER_ROOT))

from track_receiver import ContractError, ReceiverStore, validate_track_update  # noqa: E402


class ReceiverStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "receiver.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_accepts_both_sources_and_preserves_payloads(self) -> None:
        frigate = make_update("frigate", "new", "message-frigate")
        dahua = make_update("dahua", "snapshot", "message-dahua")
        dahua["source"]["instance_id"] = None
        dahua["quality"]["source_lifecycle"] = "finalized_only"

        with ReceiverStore(self.database) as store:
            self.assertTrue(store.ingest(frigate).inserted)
            self.assertTrue(store.ingest(dahua).inserted)
            self.assertEqual(list(store.iter_updates()), [frigate, dahua])
            self.assertEqual(store.count(), 2)

    def test_duplicate_message_is_idempotent(self) -> None:
        update = make_update("frigate", "update", "same-message")

        with ReceiverStore(self.database) as store:
            first = store.ingest(update)
            duplicate = store.ingest(deepcopy(update))

            self.assertTrue(first.inserted)
            self.assertFalse(duplicate.inserted)
            self.assertEqual(store.count(), 1)

    def test_invalid_message_is_rejected_before_database_write(self) -> None:
        update = make_update("frigate", "new", "invalid-message")
        update["geometry"]["box"]["x_min"] = 1.5

        with ReceiverStore(self.database) as store:
            with self.assertRaises(ContractError):
                store.ingest(update)
            self.assertEqual(store.count(), 0)

    def test_retention_removes_only_messages_older_than_seven_days(self) -> None:
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        old = make_update("dahua", "snapshot", "old-message")
        recent = make_update("frigate", "end", "recent-message")

        with ReceiverStore(self.database) as store:
            store.ingest(old, now - timedelta(days=8))
            store.ingest(recent, now - timedelta(days=6))

            self.assertEqual(store.cleanup(now), 1)
            self.assertEqual([item["message_id"] for item in store.iter_updates()], ["recent-message"])

    def test_validation_requires_timezone_and_exact_top_level_fields(self) -> None:
        update = make_update("frigate", "new", "message")
        update["published_at"] = "2026-09-15T10:00:00"
        with self.assertRaises(ContractError):
            validate_track_update(update)

    def test_rejected_payload_is_deduplicated_and_retained(self) -> None:
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        with ReceiverStore(self.database) as store:
            self.assertTrue(store.reject("tracking/track-updates", b"bad", "invalid", now))
            self.assertFalse(store.reject("tracking/track-updates", b"bad", "invalid", now))
            self.assertEqual(store.rejected_count(), 1)

            self.assertEqual(store.cleanup(now + timedelta(days=8)), 1)
            self.assertEqual(store.rejected_count(), 0)

        update = make_update("frigate", "new", "message")
        update["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_track_update(update)


def make_update(source_type: str, phase: str, message_id: str) -> dict:
    return {
        "schema_version": "track_update.v1",
        "message_id": message_id,
        "track_id": f"{source_type}:camera:test-track",
        "source": {"type": source_type, "instance_id": "test-instance"},
        "camera_id": "camera_test",
        "phase": phase,
        "sequence": 1,
        "observed_at": "2026-09-15T10:00:00+00:00",
        "published_at": "2026-09-15T10:00:00.100000+00:00",
        "subject": {
            "type": "person",
            "local_track_id": "test-track",
            "confidence": 0.9,
        },
        "geometry": {
            "coordinate_space": "normalized_0_1",
            "box": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.3, "y_max": 0.8},
            "center": {"x": 0.2, "y": 0.5},
        },
        "zones": {"current": [], "entered": []},
        "attributes": {},
        "media": [],
        "quality": {"status": "complete", "source_lifecycle": "live"},
        "source_ref": {"event_id": "source-event", "message_id": "source-message"},
    }


if __name__ == "__main__":
    unittest.main()
