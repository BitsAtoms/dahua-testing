from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.adaptive_capture import EvidenceObservation  # noqa: E402
from visual_reid.adaptive_media import (  # noqa: E402
    AdaptiveMediaStore,
    adaptive_media_revisions,
)
from visual_reid.quality import QualityAssessment  # noqa: E402


class AdaptiveMediaTests(unittest.TestCase):
    def test_saves_selected_crop_without_embedding_or_url(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "media.sqlite3"
            store = AdaptiveMediaStore(database, root / "assets")
            quality = QualityAssessment(
                modality="body",
                score=0.8,
                usable_for_embedding=True,
                strong=True,
                width=128,
                height=256,
                brightness=120.0,
                contrast=30.0,
                sharpness=100.0,
                reasons=(),
            )
            observation = EvidenceObservation(
                "observation-a",
                "track-a",
                "cam-a",
                2_000_000,
                quality,
                {
                    "kind": "buffer_frame",
                    "crop_jpeg": b"\xff\xd8data\xff\xd9",
                    "body_validity_version": "adaptive-body-observed-time.v2-live-only",
                    "alignment_delta_us": 125_000,
                },
            )

            self.assertEqual(store.save_observations((observation,)), 1)
            self.assertEqual(store.save_observations((observation,)), 0)
            store.close()

            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT track_id, camera_id, modality, path, quality_json "
                "FROM adaptive_media"
            ).fetchone()
            connection.close()
            self.assertEqual(row[:3], ("track-a", "cam-a", "body"))
            self.assertTrue(Path(row[3]).is_file())
            quality_document = json.loads(row[4])
            self.assertNotIn("vector", quality_document)
            self.assertNotIn("rtsp", row[4].lower())
            self.assertEqual(
                quality_document["body_validity_version"],
                "adaptive-body-observed-time.v2-live-only",
            )
            self.assertEqual(quality_document["alignment_delta_us"], 125_000)
            self.assertIn("track-a", adaptive_media_revisions(database, ["track-a"]))

    def test_cleanup_removes_expired_file_and_row(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "media.sqlite3"
            store = AdaptiveMediaStore(database, root / "assets")
            quality = QualityAssessment(
                modality="body",
                score=0.8,
                usable_for_embedding=True,
                strong=True,
                width=128,
                height=256,
                brightness=120.0,
                contrast=30.0,
                sharpness=100.0,
                reasons=(),
            )
            observation = EvidenceObservation(
                "old", "track", "cam", 1, quality,
                {"kind": "buffer_frame", "crop_jpeg": b"jpeg"},
            )
            store.save_observations((observation,))
            store._connection.execute("UPDATE adaptive_media SET created_us=1")
            store._connection.commit()

            removed = store.cleanup(datetime(2026, 9, 17, tzinfo=timezone.utc))

            self.assertEqual(removed, 1)
            self.assertEqual(
                store._connection.execute("SELECT COUNT(*) FROM adaptive_media").fetchone()[0],
                0,
            )
            store.close()

    def test_keeps_at_most_five_strongest_crops_per_track_modality(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "media.sqlite3"
            store = AdaptiveMediaStore(database, root / "assets")
            observations = []
            for index in range(6):
                quality = QualityAssessment(
                    modality="body",
                    score=float(index) / 10,
                    usable_for_embedding=True,
                    strong=True,
                    width=128,
                    height=256,
                    brightness=120.0,
                    contrast=30.0,
                    sharpness=100.0,
                    reasons=(),
                )
                observations.append(
                    EvidenceObservation(
                        f"observation-{index}",
                        "track-a",
                        "cam-a",
                        index,
                        quality,
                        {"kind": "buffer_frame", "crop_jpeg": b"jpeg"},
                    )
                )

            store.save_observations(tuple(observations))

            rows = store._connection.execute(
                "SELECT quality_score, path FROM adaptive_media ORDER BY quality_score"
            ).fetchall()
            self.assertEqual([row[0] for row in rows], [0.1, 0.2, 0.3, 0.4, 0.5])
            self.assertTrue(all(Path(row[1]).is_file() for row in rows))
            self.assertEqual(len(list((root / "assets" / "cam-a").glob("*.jpg"))), 5)
            store.close()


if __name__ == "__main__":
    unittest.main()
