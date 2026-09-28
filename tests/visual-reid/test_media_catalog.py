from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid import (  # noqa: E402
    iter_media_assets,
    load_track_visuals,
    summarize_assets,
)


JPEG_16X9 = (
    b"\xff\xd8"
    b"\xff\xe0\x00\x04\x00\x00"
    b"\xff\xc0\x00\x0b\x08\x00\x09\x00\x10\x01\x01\x11\x00"
    b"\xff\xd9"
)


class MediaCatalogTests(unittest.TestCase):
    def test_catalogs_distinct_existing_and_missing_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "face.jpg"
            image.write_bytes(JPEG_16X9)
            missing = root / "missing.jpg"
            database = root / "receiver.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE track_updates (
                    track_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    receiver_received_us INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            payload = {
                "track_id": "track-1",
                "source": {"type": "dahua"},
                "camera_id": "cam-a",
                "media": [
                    {"role": "face", "path": str(image)},
                    {"role": "body", "path": str(missing)},
                    {"role": "face", "path": str(image)},
                ],
            }
            connection.execute(
                "INSERT INTO track_updates VALUES (?, ?, ?, ?)",
                ("track-1", "snapshot", 1, json.dumps(payload)),
            )
            connection.commit()
            connection.close()

            assets = list(iter_media_assets(database))
            summary = summarize_assets(assets)

            self.assertEqual(len(assets), 2)
            face = next(asset for asset in assets if asset.role == "face")
            self.assertEqual((face.width, face.height), (16, 9))
            self.assertTrue(face.valid_jpeg)
            body = next(asset for asset in assets if asset.role == "body")
            self.assertFalse(body.exists)
            self.assertEqual({row["role"] for row in summary}, {"face", "body"})
            visual = load_track_visuals(
                database, ["track-1"], preferred_roles=("face",)
            )["track-1"]
            self.assertEqual(visual.role, "face")
            self.assertEqual(visual.path, image)


if __name__ == "__main__":
    unittest.main()
