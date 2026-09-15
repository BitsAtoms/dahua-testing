from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_ROOT = REPOSITORY_ROOT / "services" / "track-transport"
sys.path.insert(0, str(TRANSPORT_ROOT))

from track_transport import OutboxStore  # noqa: E402


class OutboxStoreTests(unittest.TestCase):
    def test_persists_until_delivery_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            store = OutboxStore(path)
            update = {"message_id": "message-1", "schema_version": "track_update.v1"}

            self.assertTrue(store.enqueue(update))
            self.assertFalse(store.enqueue(update))
            self.assertEqual(store.pending(), [("message-1", json.dumps(
                update,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"))])

            store.close()
            reopened = OutboxStore(path)
            self.assertEqual(reopened.count(), 1)
            reopened.delivered("message-1")
            self.assertEqual(reopened.count(), 0)
            reopened.close()

    def test_rejects_message_id_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = OutboxStore(Path(directory) / "outbox.sqlite3")
            store.enqueue({"message_id": "same", "value": 1})

            with self.assertRaisesRegex(ValueError, "collision"):
                store.enqueue({"message_id": "same", "value": 2})
            store.close()

    def test_retention_removes_only_expired_pending_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            store = OutboxStore(path)
            store.enqueue({"message_id": "old"})
            store.enqueue({"message_id": "recent"})
            now = datetime.now(timezone.utc)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE pending_updates SET enqueued_at = ? WHERE message_id = ?",
                    ((now - timedelta(days=8)).isoformat(), "old"),
                )
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(store.cleanup(days=7, now=now), 1)
            self.assertEqual(store.pending(), [("recent", b'{"message_id":"recent"}')])
            store.close()


if __name__ == "__main__":
    unittest.main()
