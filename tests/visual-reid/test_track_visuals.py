from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.adaptive_media import ADAPTIVE_BODY_VALIDITY_VERSION  # noqa: E402
from visual_reid.face_detection import ADAPTIVE_FACE_VALIDITY_VERSION  # noqa: E402
from visual_reid.track_visuals import load_track_visual_sets  # noqa: E402


class TrackVisualTests(unittest.TestCase):
    def test_loads_adaptive_face_only_for_face_role(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            receiver = root / "receiver.sqlite3"
            adaptive = root / "adaptive.sqlite3"
            face = root / "face.jpg"
            legacy_face = root / "legacy-face.jpg"
            body = root / "body.jpg"
            face.write_bytes(b"face")
            legacy_face.write_bytes(b"legacy")
            body.write_bytes(b"body")
            connection = sqlite3.connect(receiver)
            connection.execute(
                """
                CREATE TABLE track_updates (
                    track_id TEXT, receiver_received_us INTEGER,
                    payload_json TEXT
                )
                """
            )
            connection.commit()
            connection.close()
            connection = sqlite3.connect(adaptive)
            connection.execute(
                """
                CREATE TABLE adaptive_media (
                    asset_id TEXT, track_id TEXT, camera_id TEXT,
                    modality TEXT, observed_us INTEGER,
                    quality_score REAL, quality_json TEXT, path TEXT
                )
                """
            )
            connection.executemany(
                "INSERT INTO adaptive_media VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        "face",
                        "track-a",
                        "cam-a",
                        "face",
                        1,
                        0.8,
                        json.dumps(
                            {
                                "face_validity_version": (
                                    ADAPTIVE_FACE_VALIDITY_VERSION
                                )
                            }
                        ),
                        str(face),
                    ),
                    (
                        "legacy-face",
                        "track-a",
                        "cam-a",
                        "face",
                        0,
                        0.95,
                        "{}",
                        str(legacy_face),
                    ),
                    ("body", "track-a", "cam-a", "body", 2, 0.9, "{}", str(body)),
                ],
            )
            connection.commit()
            connection.close()

            visuals = load_track_visual_sets(
                receiver,
                ["track-a"],
                preferred_roles=("face",),
                adaptive_media_database=adaptive,
            )

        self.assertEqual(len(visuals["track-a"]), 1)
        self.assertEqual(visuals["track-a"][0].role, "adaptive_face")
        self.assertEqual(visuals["track-a"][0].quality_weight, 0.8)

    def test_loads_only_versioned_adaptive_body(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            receiver = root / "receiver.sqlite3"
            adaptive = root / "adaptive.sqlite3"
            current = root / "current.jpg"
            legacy = root / "legacy.jpg"
            current.write_bytes(b"current")
            legacy.write_bytes(b"legacy")
            connection = sqlite3.connect(receiver)
            connection.execute(
                "CREATE TABLE track_updates "
                "(track_id TEXT, receiver_received_us INTEGER, payload_json TEXT)"
            )
            connection.commit()
            connection.close()
            connection = sqlite3.connect(adaptive)
            connection.execute(
                """
                CREATE TABLE adaptive_media (
                    asset_id TEXT, track_id TEXT, camera_id TEXT,
                    modality TEXT, observed_us INTEGER,
                    quality_score REAL, quality_json TEXT, path TEXT
                )
                """
            )
            connection.executemany(
                "INSERT INTO adaptive_media VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        "current",
                        "track-a",
                        "cam-a",
                        "body",
                        2,
                        0.8,
                        json.dumps(
                            {"body_validity_version": ADAPTIVE_BODY_VALIDITY_VERSION}
                        ),
                        str(current),
                    ),
                    (
                        "legacy",
                        "track-a",
                        "cam-a",
                        "body",
                        1,
                        0.9,
                        "{}",
                        str(legacy),
                    ),
                ],
            )
            connection.commit()
            connection.close()

            visuals = load_track_visual_sets(
                receiver,
                ["track-a"],
                adaptive_media_database=adaptive,
            )

        self.assertEqual(len(visuals["track-a"]), 1)
        self.assertEqual(visuals["track-a"][0].path, current)


if __name__ == "__main__":
    unittest.main()
