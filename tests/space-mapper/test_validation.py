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
                    "track_classifications": {"track-a": "person"},
                    "candidate_verdicts": {"candidate-a": "same_person"},
                    "notes": "recorrido controlado",
                },
            )

            self.assertEqual(completed["status"], "complete")
            self.assertEqual(annotated["annotations"]["track_subjects"], {"track-a": "A"})
            self.assertEqual(
                annotated["annotations"]["track_classifications"],
                {"track-a": "person"},
            )
            self.assertEqual(store.cleanup(started + timedelta(days=8)), 1)
            self.assertIsNone(store.get(session["session_id"]))

    def test_reads_legacy_subject_annotations_as_person_tracks(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = ValidationStore(Path(directory) / "validation.sqlite3")
            session = store.start("test", ["cam-a"], ["A"], now)
            store.complete(
                session["session_id"],
                {"tracks": [{"track_id": "track-a"}], "candidates": []},
                now,
            )
            connection = store._connect()
            try:
                connection.execute(
                    "UPDATE validation_sessions SET annotations_json=? WHERE session_id=?",
                    ('{"track_subjects":{"track-a":"A"}}', session["session_id"]),
                )
                connection.commit()
            finally:
                connection.close()

            loaded = store.get(session["session_id"])
            self.assertEqual(
                loaded["annotations"]["track_classifications"],
                {"track-a": "person"},
            )

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
                        "track_classifications": {"track-a": "person"},
                        "candidate_verdicts": {},
                    },
                )
            with self.assertRaisesRegex(ValidationError, "unknown track"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {"other": "A"},
                        "track_classifications": {"other": "person"},
                        "candidate_verdicts": {},
                    },
                )

    def test_identity_excluded_tracks_reject_identity_verdicts(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        report = {
            "tracks": [{"track_id": "person"}, {"track_id": "robot"}],
            "candidates": [
                {
                    "candidate_id": "candidate-a",
                    "origin_track_id": "person",
                    "destination_track_id": "robot",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = ValidationStore(Path(directory) / "validation.sqlite3")
            session = store.start("test", ["cam-a", "cam-b"], ["A"], now)
            store.complete(session["session_id"], report, now)
            annotated = store.annotate(
                session["session_id"],
                {
                    "track_subjects": {"person": "A"},
                    "track_classifications": {
                        "person": "person",
                        "robot": "false_positive",
                    },
                    "candidate_verdicts": {},
                },
            )
            self.assertEqual(
                annotated["annotations"]["track_classifications"]["robot"],
                "false_positive",
            )
            with self.assertRaisesRegex(ValidationError, "identity-excluded"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {"person": "A"},
                        "track_classifications": {
                            "person": "person",
                            "robot": "false_positive",
                        },
                        "candidate_verdicts": {
                            "candidate-a": "different_person"
                        },
                    },
                )

            annotated = store.annotate(
                session["session_id"],
                {
                    "track_subjects": {"person": "A"},
                    "track_classifications": {
                        "person": "person",
                        "robot": "out_of_scope_person",
                    },
                    "candidate_verdicts": {},
                },
            )
            self.assertEqual(
                annotated["annotations"]["track_classifications"]["robot"],
                "out_of_scope_person",
            )
            with self.assertRaisesRegex(ValidationError, "identity-excluded"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {"person": "A"},
                        "track_classifications": {
                            "person": "person",
                            "robot": "out_of_scope_person",
                        },
                        "candidate_verdicts": {
                            "candidate-a": "different_person"
                        },
                    },
                )

    def test_rejects_verdict_that_conflicts_with_subject_aliases(self) -> None:
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        report = {
            "tracks": [{"track_id": "track-a"}, {"track_id": "track-b"}],
            "candidates": [
                {
                    "candidate_id": "candidate-a",
                    "origin_track_id": "track-a",
                    "destination_track_id": "track-b",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = ValidationStore(Path(directory) / "validation.sqlite3")
            session = store.start("test", ["cam-a", "cam-b"], ["A"], now)
            store.complete(session["session_id"], report, now)
            with self.assertRaisesRegex(ValidationError, "conflicts"):
                store.annotate(
                    session["session_id"],
                    {
                        "track_subjects": {
                            "track-a": "A",
                            "track-b": "A",
                        },
                        "track_classifications": {
                            "track-a": "person",
                            "track-b": "person",
                        },
                        "candidate_verdicts": {
                            "candidate-a": "different_person"
                        },
                    },
                )

if __name__ == "__main__":
    unittest.main()
