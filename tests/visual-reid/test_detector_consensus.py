from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "visual-reid"))

from visual_reid.detector_benchmark import Detection  # noqa: E402
from visual_reid.detector_consensus import (  # noqa: E402
    ConsensusAuditStore,
    ConsensusEvaluation,
    ShadowConsensusValidator,
    best_consensus_iou,
    derive_track_state,
    normalized_box_pixels,
)


class DetectorConsensusTests(unittest.TestCase):
    def test_normalized_box_converts_to_frame_pixels(self) -> None:
        result = normalized_box_pixels(
            {
                "coordinate_space": "normalized_0_1",
                "box": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.8},
            },
            1000,
            500,
        )
        self.assertEqual(result, (100.0, 100.0, 500.0, 400.0))

    def test_best_consensus_iou_ignores_unrelated_false_positive(self) -> None:
        score = best_consensus_iou(
            (100, 100, 300, 400),
            [Detection((500, 100, 700, 400), 0.9), Detection((110, 110, 290, 390), 0.8)],
        )
        self.assertGreater(score, 0.7)

    def test_live_frame_evaluation_matches_source_box(self) -> None:
        class Detector:
            def detect(self, _frame, _threshold):
                return [Detection((10, 20, 50, 80), 0.9)]

        class Frame:
            shape = (100, 100, 3)

        update = {
            "message_id": "message-1",
            "track_id": "track-1",
            "camera_id": "camera-1",
            "phase": "update",
            "subject": {"type": "person", "confidence": 0.8},
            "geometry": {
                "coordinate_space": "normalized_0_1",
                "box": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.8},
            },
        }
        result = ShadowConsensusValidator(Detector()).evaluate_frame(update, Frame())

        self.assertEqual(result.proposed_state, "eligible")
        self.assertEqual(result.max_iou, 1.0)

    def test_source_confidence_guard_can_reject_spatial_match(self) -> None:
        class Detector:
            def detect(self, _frame, _threshold):
                return [Detection((10, 20, 50, 80), 0.9)]

        class Frame:
            shape = (100, 100, 3)

        update = {
            "message_id": "message-1",
            "track_id": "track-1",
            "camera_id": "camera-1",
            "phase": "update",
            "subject": {"type": "person", "confidence": 0.4},
            "geometry": {
                "coordinate_space": "normalized_0_1",
                "box": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.8},
            },
        }
        result = ShadowConsensusValidator(Detector()).evaluate_frame(update, Frame())

        self.assertEqual(result.proposed_state, "excluded")
        self.assertEqual(result.reason, "source_confidence_below_guard")

    def test_audit_store_upserts_and_expires_shadow_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConsensusAuditStore(Path(directory) / "audit.sqlite3")
            evaluation = ConsensusEvaluation(
                message_id="message-1",
                track_id="track-1",
                camera_id="camera-1",
                phase="update",
                proposed_state="eligible",
                reason="detector_spatial_consensus",
                evaluated=True,
                source_confidence=0.9,
                source_box=(0, 0, 10, 20),
                candidate_boxes=((1, 1, 11, 21),),
                candidate_scores=(0.8,),
                max_iou=0.75,
                guard_threshold=0.5,
                candidate_threshold=0.4,
                iou_threshold=0.3,
                latency_ms=20.0,
                frame_width=100,
                frame_height=100,
                media_path="snapshot.jpg",
                frame_observed_us=1,
                alignment_delta_us=0,
            )
            old = datetime(2026, 9, 1, tzinfo=timezone.utc)
            store.save(evaluation, round(old.timestamp() * 1_000_000))
            self.assertEqual(store.counts(), {"eligible": 1})
            self.assertEqual(store.track_state("track-1")["state"], "provisional")

            removed = store.cleanup(old + timedelta(days=8))

            self.assertEqual(removed, 1)
            self.assertEqual(store.counts(), {})
            store.close()

    def test_track_state_detects_sustained_detector_change(self) -> None:
        self.assertEqual(derive_track_state(["excluded"] * 5), "excluded")
        self.assertEqual(
            derive_track_state(["excluded"] * 5 + ["eligible"] * 5),
            "contaminated",
        )

    def test_track_state_tolerates_isolated_miss(self) -> None:
        self.assertEqual(
            derive_track_state(
                ["eligible", "eligible", "excluded", "eligible", "eligible"]
            ),
            "eligible",
        )

    def test_track_state_keeps_eligibility_during_short_exit_noise(self) -> None:
        self.assertEqual(
            derive_track_state(["eligible"] * 5 + ["excluded"] * 3),
            "eligible",
        )

    def test_track_state_does_not_exclude_four_initial_alignment_misses(self) -> None:
        self.assertEqual(
            derive_track_state(["excluded"] * 4 + ["eligible"] * 3),
            "provisional",
        )

    def test_track_state_detects_reverse_sustained_change(self) -> None:
        self.assertEqual(
            derive_track_state(["eligible"] * 5 + ["excluded"] * 5),
            "contaminated",
        )


if __name__ == "__main__":
    unittest.main()
