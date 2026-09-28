import importlib.util
from enum import IntEnum
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "windows-onnx-gpu"
sys.path.insert(0, str(EXPERIMENT))

from benchmark_lib import BenchmarkError, analyze_profile, percentile, ready_status_is_success


class ReadyResultState(IntEnum):
    IN_PROGRESS = 0
    SUCCESS = 1
    FAILURE = 2


class ReadyStatusTests(unittest.TestCase):
    def test_accepts_success_enum_and_integer(self):
        self.assertTrue(ready_status_is_success(ReadyResultState.SUCCESS, ReadyResultState.SUCCESS))
        self.assertTrue(ready_status_is_success(1, ReadyResultState.SUCCESS))

    def test_rejects_failure_in_progress_and_unknown_status(self):
        for status in (ReadyResultState.FAILURE, 2, ReadyResultState.IN_PROGRESS, 0, 3, "1", True):
            with self.subTest(status=status):
                self.assertFalse(ready_status_is_success(status, ReadyResultState.SUCCESS))


class PercentileTests(unittest.TestCase):
    def test_interpolates_percentiles(self):
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)


class ProfileTests(unittest.TestCase):
    def test_accepts_requested_provider_without_cpu_fallback(self):
        result = analyze_profile(
            [{"cat": "Node", "args": {"provider": "MIGraphXExecutionProvider"}}],
            "MIGraphXExecutionProvider",
        )
        self.assertTrue(result["requested_provider_used"])
        self.assertFalse(result["cpu_fallback"])

    def test_detects_partial_cpu_fallback(self):
        result = analyze_profile(
            [
                {"args": {"provider": "DmlExecutionProvider"}},
                {"args": {"provider": "CPUExecutionProvider"}},
            ],
            "DmlExecutionProvider",
        )
        self.assertTrue(result["requested_provider_used"])
        self.assertTrue(result["cpu_fallback"])

    def test_detects_total_cpu_fallback(self):
        result = analyze_profile(
            [{"args": {"provider": "CPUExecutionProvider"}}],
            "MIGraphXExecutionProvider",
        )
        self.assertFalse(result["requested_provider_used"])
        self.assertTrue(result["cpu_fallback"])

    def test_rejects_profile_without_assignments(self):
        with self.assertRaises(BenchmarkError):
            analyze_profile([{"args": {}}], "CPUExecutionProvider")


class CpuIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("numpy") and importlib.util.find_spec("onnxruntime"),
        "CPU runtime dependencies are not installed",
    )
    def test_fixture_runs_on_cpu_and_reports_real_provider(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(EXPERIMENT / "benchmark.py"),
                "run",
                "--provider",
                "cpu",
                "--warmup",
                "1",
                "--iterations",
                "2",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["provider_real"], ["CPUExecutionProvider"])
        self.assertFalse(report["cpu_fallback"])


if __name__ == "__main__":
    unittest.main()
