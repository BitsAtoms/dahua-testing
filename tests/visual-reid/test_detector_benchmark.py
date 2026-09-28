from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "visual-reid"))

from visual_reid.detector_benchmark import (  # noqa: E402
    Detection,
    _yolox_grids,
    consensus_detections,
    detection_runs,
    non_max_suppression,
)


class DetectorBenchmarkTests(unittest.TestCase):
    def test_detection_runs_bridges_single_missing_frame(self) -> None:
        self.assertEqual(detection_runs([True, False, True, False, False, True]), 2)

    def test_nms_removes_overlapping_lower_score(self) -> None:
        result = non_max_suppression([
            Detection((0, 0, 10, 10), 0.9),
            Detection((1, 1, 10, 10), 0.8),
            Detection((20, 20, 30, 30), 0.7),
        ])
        self.assertEqual([item.score for item in result], [0.9, 0.7])

    def test_yolox_grid_matches_tiny_output(self) -> None:
        grid, strides = _yolox_grids(416, 416)
        self.assertEqual(grid.shape, (3549, 2))
        self.assertEqual(strides.shape, (3549, 1))
        self.assertTrue(np.all(strides[: 52 * 52] == 8))

    def test_consensus_keeps_only_spatially_supported_detection(self) -> None:
        result = consensus_detections(
            [Detection((0, 0, 10, 20), 0.8), Detection((30, 0, 40, 20), 0.9)],
            [Detection((1, 1, 11, 21), 0.7)],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].score, 0.7)
        self.assertEqual(result[0].box, (0.5, 0.5, 10.5, 20.5))

    def test_consensus_rejects_weak_overlap(self) -> None:
        result = consensus_detections(
            [Detection((0, 0, 10, 10), 0.9)],
            [Detection((8, 8, 18, 18), 0.9)],
            iou_threshold=0.3,
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
