from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKING_ROOT = REPOSITORY_ROOT / "services" / "tracking-engine"
RECEIVER_ROOT = REPOSITORY_ROOT / "services" / "track-receiver"
sys.path.insert(0, str(TRACKING_ROOT))
sys.path.insert(0, str(RECEIVER_ROOT))

from track_receiver import ReceiverStore, validate_track_update  # noqa: E402
from tracking_engine import TrackingRunner, TrackingStore  # noqa: E402
from test_store import NOW, update  # noqa: E402


class TrackingRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.receiver_path = root / "receiver.sqlite3"
        self.receiver = ReceiverStore(self.receiver_path)
        self.store = TrackingStore(root / "tracking.sqlite3")
        self.runner = TrackingRunner(
            self.receiver_path, self.store, validate_track_update, batch_size=2
        )

    def tearDown(self) -> None:
        self.store.close()
        self.receiver.close()
        self.temporary.cleanup()

    def test_resumes_from_durable_cursor_and_catches_up_new_rows(self) -> None:
        self.receiver.ingest(update("new", 1), NOW)
        self.receiver.ingest(update("update", 2), NOW + timedelta(seconds=1))
        self.receiver.ingest(update("end", 3), NOW + timedelta(seconds=2))

        first = self.runner.poll()
        second = self.runner.poll()
        idle = self.runner.poll()

        self.assertEqual((first.scanned, second.scanned, idle.scanned), (2, 1, 0))
        self.assertEqual(self.runner.pending_count(), 0)
        self.assertEqual(
            self.store.get_track("frigate:cam-a:track-1")["status"], "ended"
        )

        later = update("new", 4)
        later["message_id"] = "message-new-track-2"
        later["track_id"] = "frigate:cam-a:track-2"
        later["subject"]["local_track_id"] = "track-2"
        self.receiver.ingest(later, NOW + timedelta(seconds=3))

        resumed = TrackingRunner(
            self.receiver_path, self.store, validate_track_update, batch_size=2
        ).poll()
        self.assertEqual(resumed.scanned, 1)
        self.assertEqual(self.store.count(), 2)

    def test_missing_receiver_fails_loudly(self) -> None:
        runner = TrackingRunner(
            Path(self.temporary.name) / "missing.sqlite3",
            self.store,
            validate_track_update,
        )
        with self.assertRaises(FileNotFoundError):
            runner.poll()

    def test_projection_callback_runs_only_for_newly_applied_messages(self) -> None:
        calls: list[str] = []
        self.receiver.ingest(update("new", 1), NOW)
        runner = TrackingRunner(
            self.receiver_path,
            self.store,
            validate_track_update,
            on_projected=lambda message, _result: calls.append(message["message_id"])
            or 0,
        )

        runner.poll()
        runner.poll()

        self.assertEqual(calls, ["message-new-1"])


if __name__ == "__main__":
    unittest.main()
