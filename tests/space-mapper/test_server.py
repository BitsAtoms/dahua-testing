from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "services" / "space-mapper"
sys.path.insert(0, str(SERVICE_ROOT))

from server import MapServer  # noqa: E402


class MediaProjectionTests(unittest.TestCase):
    def test_repeated_track_uses_independent_adaptive_media_copies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "adaptive.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE adaptive_media (
                    track_id TEXT, modality TEXT, observed_us INTEGER,
                    quality_score REAL, path TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO adaptive_media VALUES (?,?,?,?,?)",
                ("track-a", "face", 1, 0.9, "missing.jpg"),
            )
            connection.commit()
            connection.close()

            server = MapServer.__new__(MapServer)
            server.adaptive_media_database = database
            server.media = {}
            server.media_lock = threading.Lock()
            result = server.with_media_urls(
                {
                    "recent": [
                        {"tracks": [{"track_id": "track-a", "media": []}]},
                        {"tracks": [{"track_id": "track-a", "media": []}]},
                    ]
                }
            )

            first, second = result["recent"]
            self.assertEqual(first["tracks"][0]["media"][0]["role"], "adaptive_face")
            self.assertEqual(second["tracks"][0]["media"][0]["role"], "adaptive_face")


if __name__ == "__main__":
    unittest.main()
