from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT))

from analyze_deepstream_tracks import (  # noqa: E402
    build_report,
    parse_track_directory,
)


class AnalyzeDeepStreamTracksTests(unittest.TestCase):
    def test_parses_split_and_inline_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            values = "7 0 0 0 10 20 30 60 0 0 0 0 0 0 0 0.75"
            (root / "00_000_000010.txt").write_text(
                f"Person\n {values}\nFace 8 0 0 0 1 2 3 4 0 0 0 0 0 0 0 0.5\n",
                encoding="utf-8",
            )

            observations = parse_track_directory(root)

        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].track_id, 7)
        self.assertEqual(observations[0].box, (10.0, 20.0, 30.0, 60.0))
        self.assertEqual(observations[1].label, "Face")

    def test_builds_per_track_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = "Person\n 3 0 0 0 {left} 20 {right} 60 0 0 0 0 0 0 0 0.8\n"
            (root / "00_000_000001.txt").write_text(
                row.format(left=10, right=30), encoding="utf-8"
            )
            (root / "00_000_000004.txt").write_text(
                row.format(left=30, right=50), encoding="utf-8"
            )
            observations = parse_track_directory(root)

        report = build_report(observations, label="Person", file_count=2)

        self.assertEqual(report["track_count"], 1)
        self.assertEqual(report["tracks"][0]["maximum_frame_gap"], 3)
        self.assertEqual(report["tracks"][0]["first_center_x"], 20.0)
        self.assertEqual(report["tracks"][0]["last_center_x"], 40.0)


if __name__ == "__main__":
    unittest.main()
