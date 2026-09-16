from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "services" / "space-mapper"
sys.path.insert(0, str(SERVICE_ROOT))

from space_mapper.monitor import monitor_snapshot  # noqa: E402


class MonitorTests(unittest.TestCase):
    def test_snapshot_joins_recent_tracks_with_visual_candidate_evidence(self) -> None:
        now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
        observed_us = round(now.timestamp() * 1_000_000)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracking = root / "tracking.sqlite3"
            evidence = root / "evidence.sqlite3"
            self._tracking_fixture(tracking, now.isoformat(), observed_us)
            self._evidence_fixture(evidence, now.isoformat(), observed_us)

            snapshot = monitor_snapshot(
                tracking,
                evidence,
                {"cam_a": "room_a", "cam_b": "room_b"},
                now=now,
            )

        self.assertEqual(snapshot["status"], "ok")
        self.assertFalse(snapshot["identity_assignment_enabled"])
        self.assertEqual(len(snapshot["tracks"]), 2)
        tracks = {item["track_id"]: item for item in snapshot["tracks"]}
        self.assertEqual(tracks["track-b"]["space_id"], "room_b")
        candidate = snapshot["candidates"][0]
        self.assertEqual(candidate["visual_ranking"], 0.72)
        self.assertEqual(candidate["available_modalities"], ["face"])
        self.assertIsNone(candidate["identity_decision"])

    @staticmethod
    def _tracking_fixture(path: Path, timestamp: str, timestamp_us: int) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE local_tracks (
                track_id TEXT, source_type TEXT, camera_id TEXT,
                local_track_id TEXT, subject_type TEXT, status TEXT,
                first_observed_at TEXT, last_observed_at TEXT, ended_at TEXT,
                last_received_at TEXT, last_received_us INTEGER,
                last_phase TEXT, media_json TEXT
            );
            CREATE TABLE handoff_candidates (
                candidate_id TEXT, origin_track_id TEXT,
                destination_track_id TEXT, origin_space_id TEXT,
                destination_space_id TEXT, gap_seconds REAL, score REAL,
                observed_at TEXT, observed_us INTEGER
            );
            """
        )
        for track_id, camera, status in (
            ("track-a", "cam_a", "ended"),
            ("track-b", "cam_b", "active"),
        ):
            connection.execute(
                "INSERT INTO local_tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    track_id,
                    "test",
                    camera,
                    track_id,
                    "person",
                    status,
                    timestamp,
                    timestamp,
                    timestamp if status == "ended" else None,
                    timestamp,
                    timestamp_us,
                    "update",
                    "[]",
                ),
            )
        connection.execute(
            "INSERT INTO handoff_candidates VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "candidate-1",
                "track-a",
                "track-b",
                "room_a",
                "room_b",
                3.2,
                0.8,
                timestamp,
                timestamp_us,
            ),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _evidence_fixture(path: Path, timestamp: str, timestamp_us: int) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE candidate_evidence (
                candidate_id TEXT, evidence_json TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO candidate_evidence VALUES (?, ?)",
            (
                "candidate-1",
                json.dumps(
                    {
                        "ranking_score": 0.72,
                        "visual_coverage": 0.5,
                        "available_modalities": ["face"],
                    }
                ),
            ),
        )
        connection.commit()
        connection.close()


if __name__ == "__main__":
    unittest.main()
