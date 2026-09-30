"""Replay bench: config generation and presence metrics from Frigate events."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = REPOSITORY_ROOT / "experiments" / "frigate-replay-bench"
sys.path.insert(0, str(BENCH_ROOT))

import bench  # noqa: E402

SCENARIO = {"name": "cam", "group": "g", "expected_person": "mixed", "expected_tracks": 2,
            "duration_s": 10.0, "detect_size": [704, 576], "path": "s/cam.mkv"}


def event(kind: str, track_id: str, frame_time: float, **after: object) -> tuple[float, dict]:
    body = {"id": track_id, "camera": "cam", "label": "person", "frame_time": frame_time,
            "false_positive": False, "top_score": 0.8}
    body.update(after)
    return frame_time, {"type": kind, "after": body}


class BenchConfigTests(unittest.TestCase):
    def test_config_loops_each_recording_as_a_detect_camera(self) -> None:
        profiles = json.loads((BENCH_ROOT / "profiles.json").read_text(encoding="utf-8"))["profiles"]
        config = bench.build_config(profiles["openvino-mobilenet"], [SCENARIO])
        camera = config["cameras"]["cam"]
        self.assertEqual(camera["ffmpeg"]["inputs"][0]["path"], "/bench/s/cam.mkv")
        self.assertIn("-stream_loop -1", camera["ffmpeg"]["inputs"][0]["input_args"])
        self.assertEqual((camera["detect"]["width"], camera["detect"]["height"]), (704, 576))
        self.assertFalse(config["record"]["enabled"])
        self.assertEqual(config["version"], "0.17-0")

    def test_scenarios_and_profiles_are_consistent(self) -> None:
        scenarios = json.loads((BENCH_ROOT / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
        self.assertEqual(len({s["name"] for s in scenarios}), 7)
        for scenario in scenarios:
            self.assertIn(scenario["expected_person"], {"absent", "present", "mixed"})
            self.assertEqual(scenario["expected_tracks"] == 0, scenario["expected_person"] == "absent")
        profiles = json.loads((BENCH_ROOT / "profiles.json").read_text(encoding="utf-8"))["profiles"]
        for name, profile in profiles.items():
            if "model_file" in profile:
                self.assertTrue(profile["model"]["path"].endswith(profile["model_file"]), name)


class PresenceMetricTests(unittest.TestCase):
    def test_tracks_ignore_false_positives_and_other_labels(self) -> None:
        messages = [
            event("new", "a", 100.0),
            event("update", "a", 103.0, top_score=0.9),
            event("end", "a", 105.0, end_time=105.0),
            event("new", "fp", 101.0, false_positive=True),
            event("new", "car", 101.0, label="car"),
        ]
        tracks = bench.person_tracks(messages)
        self.assertEqual(set(tracks), {"a"})
        self.assertEqual((tracks["a"].start, tracks["a"].end, tracks["a"].top_score), (100.0, 105.0, 0.9))

    def test_per_second_counts_overlap_and_open_tracks(self) -> None:
        tracks = [bench.Track("cam", 100.0, 103.0), bench.Track("cam", 102.0, None)]
        self.assertEqual(bench.per_second_counts(tracks, 100.0, 105.0), [1, 1, 2, 1, 1])

    def test_scenario_metrics_normalise_tracks_per_loop(self) -> None:
        tracks = [bench.Track("cam", 101.0, 104.0, 0.7), bench.Track("cam", 103.0, 106.0, 0.9),
                  bench.Track("cam", 111.0, 113.0, 0.8)]
        row = bench.scenario_metrics(SCENARIO, tracks, 100.0, 120.0)
        self.assertEqual(row["loops"], 2.0)
        self.assertEqual(row["tracks"], 3)
        self.assertEqual(row["tracks_per_loop"], 1.5)
        self.assertEqual(row["max_simultaneous"], 2)
        self.assertEqual(row["person_seconds"], 7)
        self.assertEqual(row["median_top_score"], 0.8)


if __name__ == "__main__":
    unittest.main()
