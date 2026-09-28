from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.face_detection import (  # noqa: E402
    adaptive_face_confidence_rejection_reason,
    adaptive_face_rejection_reason,
    adaptive_landmark_rejection_reason,
    parse_face_detections,
)


class FaceDetectionTests(unittest.TestCase):
    def test_adaptive_confidence_rejects_live_couch_negatives(self) -> None:
        self.assertEqual(
            adaptive_face_confidence_rejection_reason(0.166222),
            "adaptive_face_low_confidence",
        )
        self.assertEqual(
            adaptive_face_confidence_rejection_reason(0.158017),
            "adaptive_face_low_confidence",
        )
        self.assertIsNone(adaptive_face_confidence_rejection_reason(0.468395))

    def test_adaptive_validity_rejects_captured_hard_negative_geometry(self) -> None:
        self.assertEqual(
            adaptive_face_rejection_reason(42, 270),
            "adaptive_face_invalid_aspect",
        )
        self.assertEqual(
            adaptive_face_rejection_reason(33, 38),
            "adaptive_face_too_narrow",
        )
        self.assertEqual(
            adaptive_face_rejection_reason(24, 35),
            "adaptive_face_too_narrow",
        )
        self.assertEqual(
            adaptive_face_rejection_reason(
                46,
                85,
                container_width=61,
                container_height=85,
            ),
            "adaptive_face_oversized_in_body",
        )

    def test_adaptive_validity_keeps_observed_faces(self) -> None:
        self.assertIsNone(
            adaptive_face_rejection_reason(
                80,
                133,
                container_width=395,
                container_height=903,
            )
        )

    def test_landmarks_reject_live_floor_and_leg_negatives(self) -> None:
        floor = [
            [0.1697, 0.5783],
            [0.5926, 0.4017],
            [0.3522, 0.7558],
            [0.3566, 0.9240],
            [0.6727, 0.7864],
        ]
        leg = [
            [0.1601, 0.3696],
            [0.6537, 0.4350],
            [0.2463, 0.5916],
            [0.0806, 0.7465],
            [0.4756, 0.8284],
        ]

        self.assertEqual(
            adaptive_landmark_rejection_reason(floor),
            "adaptive_face_landmark_geometry",
        )
        self.assertEqual(
            adaptive_landmark_rejection_reason(leg),
            "adaptive_face_landmark_geometry",
        )

    def test_landmarks_keep_live_meetings_face(self) -> None:
        face = [
            [0.3858, 0.3526],
            [0.7529, 0.4496],
            [0.5452, 0.4567],
            [0.2371, 0.6312],
            [0.5463, 0.7236],
        ]

        self.assertIsNone(adaptive_landmark_rejection_reason(face))
        self.assertIsNone(
            adaptive_face_rejection_reason(
                51,
                89,
                container_width=173,
                container_height=318,
            )
        )

    def test_landmarks_reject_live_wall_negative(self) -> None:
        wall = [
            [0.208793, 0.475309],
            [0.581853, 0.337154],
            [0.337197, 0.693957],
            [0.344467, 0.861650],
            [0.663248, 0.738639],
        ]

        self.assertEqual(
            adaptive_landmark_rejection_reason(wall),
            "adaptive_face_landmark_geometry",
        )

    def test_parses_confident_face_and_rejects_small_or_weak_boxes(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        output = np.array(
            [
                [0, 1, 0.91, 0.25, 0.20, 0.55, 0.70],
                [0, 1, 0.10, 0.10, 0.10, 0.80, 0.80],
                [0, 1, 0.99, 0.01, 0.01, 0.05, 0.05],
                [-1, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.float32,
        )

        boxes = parse_face_detections(output, 200, 300)

        self.assertEqual(len(boxes), 1)
        confidence, left, top, right, bottom = boxes[0]
        self.assertAlmostEqual(confidence, 0.91, places=5)
        self.assertLess(left, 50)
        self.assertLess(top, 60)
        self.assertGreater(right, 110)
        self.assertGreater(bottom, 210)


if __name__ == "__main__":
    unittest.main()
