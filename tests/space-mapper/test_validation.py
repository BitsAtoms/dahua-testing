from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "services" / "space-mapper"
sys.path.insert(0, str(SERVICE_ROOT))

from space_mapper.validation import ValidationError, ValidationStore  # noqa: E402


class ValidationStoreTests(unittest.TestCase):
    def test_session_report_annotations_and_retention(self) -> None:
        started = datetime(2026, 9, 16, 10, tzinfo=timezone.utc)
        report = {
            "tracks": [{"track_id": "track-a", "media": []}],
            "candidates": [{"candidate_id": "candidate-a"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = ValidationStore(Path(directory) / "validation.sqlite3")
            session = store.start(
                "ida y vuelta",
                ["dahua_213", "recepcion", "dahua_213"],
                ["A"],
                started,
            )
            with self.assertRaisesRegex(ValidationError, "already active"):
                store.start("otra", ["dahua_213"], ["B"], started)
            completed = store.complete(
                session["session_id"], report, started + timedelta(minutes=1)
            )
            annotated = store.annotate(
                session["session_id"],
                {
                    "track_subjects": {"track-a": "A"},
                    "candidate_verdicts": {"candidate-a": "same_person"},
                    "notes": "recorrido controlado",
                },
            )

            self.assertEqual(completed["status"], "complete")
            self.assertEqual(annotated["annotations"]["track_subjects"], {"track-a": "A"})
            self.assertEqual(store.cleanup(started + timedelta(days=8)), 1)
            self.assertIsNone(store.get(session["session_id"]))

    def test_rejects_unknown_annotation_targets_and_aliases(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = ValidationStore(Path(directory) / "validation.sqlite3")
            session = store.start("test", ["cam-a"], ["A"], now)
            store.complete(
                session["session_id"],
                {"tracks": [{"track_id": "track-a"}], "candidates": []},
                now,
            )
            with self.assertRaisesRegex(ValidationError, "unknown subject alias"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {"track-a": "B"},
                        "candidate_verdicts": {},
                    },
                )
            with self.assertRaisesRegex(ValidationError, "unknown track"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {"other": "A"},
                        "candidate_verdicts": {},
                    },
                )


if __name__ == "__main__":
    unittest.main()
