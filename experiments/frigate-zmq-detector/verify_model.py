#!/usr/bin/env python3
"""Check that an ONNX detector runs entirely on the requested provider.

Runs the model with ONNX Runtime profiling, fails if any node executed on the
CPU provider, compares its outputs with a CPU run and reports latency. This is
the same acceptance rule as experiments/windows-onnx-gpu: a session that was
merely created on the GPU does not count.

  experiments\\frigate-zmq-detector\\.venv\\Scripts\\python.exe \\
    experiments\\frigate-zmq-detector\\verify_model.py MODEL.onnx --provider DmlExecutionProvider
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import onnxruntime as ort

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "experiments" / "windows-onnx-gpu"))

from benchmark_lib import analyze_profile, percentile  # noqa: E402


def session(model: Path, provider: str, profile_dir: Path | None, device_id: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    if profile_dir is not None:
        options.enable_profiling = True
        options.profile_file_prefix = str(profile_dir / "profile")
    provider_options = {"device_id": str(device_id)} if provider == "DmlExecutionProvider" else {}
    return ort.InferenceSession(str(model), sess_options=options, providers=[(provider, provider_options)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", type=Path)
    parser.add_argument("--provider", default="DmlExecutionProvider")
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()

    reference = session(args.model, "CPUExecutionProvider", None, 0)
    shape = [int(d) for d in reference.get_inputs()[0].shape]
    pixels = np.random.default_rng(20260930).random(shape, dtype=np.float32)
    feed_name = reference.get_inputs()[0].name
    expected = reference.run(None, {feed_name: pixels})

    with tempfile.TemporaryDirectory() as directory:
        gpu = session(args.model, args.provider, Path(directory), args.device_id)
        actual = gpu.run(None, {feed_name: pixels})
        profile = Path(gpu.end_profiling())
        placement = analyze_profile(json.loads(profile.read_text(encoding="utf-8")), args.provider)

    timed = session(args.model, args.provider, None, args.device_id)
    for _ in range(5):
        timed.run(None, {feed_name: pixels})
    latencies = []
    for _ in range(args.runs):
        start = time.perf_counter()
        timed.run(None, {feed_name: pixels})
        latencies.append((time.perf_counter() - start) * 1000.0)

    difference = max(float(np.max(np.abs(a - e))) for a, e in zip(actual, expected))
    report = {
        "model": args.model.name,
        "provider": args.provider,
        "device_id": args.device_id,
        "onnxruntime": ort.__version__,
        "input_shape": shape,
        **placement,
        "max_abs_diff_vs_cpu": difference,
        "latency_ms_p50": round(percentile(latencies, 0.50), 2),
        "latency_ms_p95": round(percentile(latencies, 0.95), 2),
        "latency_ms_p99": round(percentile(latencies, 0.99), 2),
    }
    print(json.dumps(report, indent=2))
    passed = placement["requested_provider_used"] and not placement["cpu_fallback"] and difference <= args.tolerance
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
