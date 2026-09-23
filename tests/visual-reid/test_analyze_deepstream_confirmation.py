from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT))

from analyze_deepstream_confirmation import (  # noqa: E402
    Detection,
    build_confirmation_report,
    intersection_over_union,
    parse_detection_directory,
)
from analyze_deepstream_tracks import TrackObservation  # noqa: E402


class DeepStreamConfirmationTests(unittest.TestCase):
    def test_parses_detector_kitti_pairs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00_000_000012.txt").write_text(
                "Person\n 0 0 0 10 20 30 60 0 0 0 0 0 0 0 0.9\n",
                encoding="utf-8",
            )
            detections = parse_detection_directory(root)

        self.assertEqual(detections[0].frame_index, 12)
        self.assertEqual(detections[0].box, (10.0, 20.0, 30.0, 60.0))
        self.assertEqual(detections[0].confidence, 0.9)

    def test_reports_confirmed_and_unconfirmed_tracks(self):
        tracks = [
            TrackObservation(1, "Person", 7, (10, 20, 30, 60), 0.8),
            TrackObservation(2, "Person", 7, (10, 20, 30, 60), 0.8),
            TrackObservation(1, "Person", 8, (100, 100, 120, 140), 0.8),
        ]
        detections = [Detection(1, "Person", (10, 20, 30, 60), 0.9)]

        report = build_confirmation_report(
            tracks,
            detections,
            track_label="Person",
            detection_label="Person",
            iou_threshold=0.3,
        )

        self.assertEqual(report["tracks"][0]["confirmed_observation_count"], 1)
        self.assertEqual(report["tracks"][0]["confirmation_ratio"], 0.5)
        self.assertEqual(report["tracks"][1]["confirmed_observation_count"], 0)

    def test_iou_handles_disjoint_boxes(self):
        self.assertEqual(intersection_over_union((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)


if __name__ == "__main__":
    unittest.main()
