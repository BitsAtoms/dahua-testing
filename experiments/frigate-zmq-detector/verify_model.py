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
from collections import Counter
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


def build_feeds(reference: ort.InferenceSession) -> dict[str, np.ndarray]:
    """A random image in [0, 1]; an int64 input gets the image size (D-FINE)."""
    inputs = reference.get_inputs()
    image = inputs[0]
    shape = [d if isinstance(d, int) and d > 0 else 1 for d in image.shape]
    feeds = {image.name: np.random.default_rng(20260930).random(shape, dtype=np.float32)}
    for extra in inputs[1:]:
        if extra.type != "tensor(int64)":
            raise ValueError(f"unsupported extra input {extra.name} {extra.type}")
        feeds[extra.name] = np.array([[shape[2], shape[3]]], dtype=np.int64)
    return feeds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", type=Path)
    parser.add_argument("--provider", default="DmlExecutionProvider")
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()

    reference = session(args.model, "CPUExecutionProvider", None, 0)
    feeds = build_feeds(reference)
    shape = list(next(iter(feeds.values())).shape)
    expected = reference.run(None, feeds)

    with tempfile.TemporaryDirectory() as directory:
        gpu = session(args.model, args.provider, Path(directory), args.device_id)
        actual = gpu.run(None, feeds)
        profile = Path(gpu.end_profiling())
        events = json.loads(profile.read_text(encoding="utf-8"))
        placement = analyze_profile(events, args.provider)
        cpu_ops = Counter(
            (event.get("args") or {}).get("op_name") for event in events
            if (event.get("args") or {}).get("provider") == "CPUExecutionProvider"
        )

    timed = session(args.model, args.provider, None, args.device_id)
    for _ in range(5):
        timed.run(None, feeds)
    latencies = []
    for _ in range(args.runs):
        start = time.perf_counter()
        timed.run(None, feeds)
        latencies.append((time.perf_counter() - start) * 1000.0)

    # Order-insensitive: detectors that sort by score may swap near-ties.
    difference = max(
        float(np.max(np.abs(np.sort(np.asarray(a, dtype=np.float64).ravel())
                            - np.sort(np.asarray(e, dtype=np.float64).ravel()))))
        for a, e in zip(actual, expected)
    )
    report = {
        "model": args.model.name,
        "provider": args.provider,
        "device_id": args.device_id,
        "onnxruntime": ort.__version__,
        "input_shape": shape,
        **placement,
        "cpu_ops": dict(cpu_ops) if args.provider != "CPUExecutionProvider" else {},
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
