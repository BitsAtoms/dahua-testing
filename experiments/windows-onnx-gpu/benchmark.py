from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from benchmark_lib import (
    BenchmarkError,
    PROVIDER_NAMES,
    analyze_profile,
    append_jsonl,
    describe_ep_devices,
    ensure_and_register_provider,
    open_winml_catalog,
    percentile,
    select_ep_device,
    system_info,
    unregister,
    utc_now,
)


ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "models" / "matmul_relu.onnx"
MODEL_MANIFEST = ROOT / "model-manifest.json"


def load_runtime():
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise BenchmarkError("Missing runtime dependencies; run bootstrap.ps1") from exc
    return np, ort


def inspect(args: argparse.Namespace) -> int:
    _, ort = load_runtime()
    catalog = None
    registered: list[str] = []
    try:
        catalog_rows = []
        catalog_error = None
        if os.name == "nt":
            if args.prepare_provider == "migraphx":
                provider_name = PROVIDER_NAMES[args.prepare_provider]
                catalog, registered = ensure_and_register_provider(ort, provider_name)
            else:
                try:
                    catalog = open_winml_catalog()
                except BenchmarkError as exc:
                    catalog_error = str(exc)
            if catalog:
                catalog_rows = catalog.providers
        record = {
            "schema": "windows_onnx_gpu.inventory.v1",
            "kind": "inventory",
            "system": system_info(),
            "ort_version": ort.__version__,
            "available_providers": list(ort.get_available_providers()),
            "winml_catalog": catalog_rows,
            "winml_catalog_error": catalog_error,
            "ep_devices": describe_ep_devices(ort),
        }
        append_jsonl(args.output, record)
        return 0
    finally:
        unregister(ort, registered)
        if catalog:
            catalog.close()


def run_once(args: argparse.Namespace) -> dict:
    np, ort = load_runtime()
    if not MODEL.exists():
        raise BenchmarkError(f"Model fixture is missing: {MODEL}")
    manifest = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    model_sha256 = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    if model_sha256 != manifest["sha256"]:
        raise BenchmarkError(
            f"Model fixture checksum mismatch: expected {manifest['sha256']}, got {model_sha256}"
        )

    requested_name = PROVIDER_NAMES[args.provider]
    catalog = None
    registered: list[str] = []
    profile_path = None
    try:
        if args.provider == "migraphx":
            catalog, registered = ensure_and_register_provider(ort, requested_name)

        options = ort.SessionOptions()
        options.enable_profiling = True
        profile_prefix = str(Path(tempfile.gettempdir()) / f"windows-onnx-gpu-{os.getpid()}")
        options.profile_file_prefix = profile_prefix
        selected_device = None

        if args.provider == "cpu":
            start = time.perf_counter()
            session = ort.InferenceSession(
                str(MODEL), sess_options=options, providers=[requested_name]
            )
        else:
            if not hasattr(ort, "get_ep_devices") or not hasattr(options, "add_provider_for_devices"):
                raise BenchmarkError("This ONNX Runtime lacks explicit OrtEpDevice selection APIs")
            selected_device = select_ep_device(ort, requested_name, args.device_index)
            options.add_provider_for_devices([selected_device], {})
            start = time.perf_counter()
            session = ort.InferenceSession(str(MODEL), sess_options=options)
        init_ms = (time.perf_counter() - start) * 1000.0

        input_array = np.arange(4096, dtype=np.float32).reshape(64, 64) / 4096.0
        expected = np.maximum(input_array @ np.eye(64, dtype=np.float32) + 0.25, 0.0)
        for _ in range(args.warmup):
            output = session.run(None, {"input": input_array})[0]

        latencies: list[float] = []
        total_start = time.perf_counter()
        for _ in range(args.iterations):
            run_start = time.perf_counter()
            output = session.run(None, {"input": input_array})[0]
            latencies.append((time.perf_counter() - run_start) * 1000.0)
        elapsed = time.perf_counter() - total_start
        max_abs_error = float(np.max(np.abs(output - expected)))
        if not np.allclose(output, expected, rtol=1e-5, atol=1e-6):
            raise BenchmarkError(f"Model output verification failed (max abs error {max_abs_error})")

        session_providers = list(session.get_providers())
        profile_path = Path(session.end_profiling())
        del session
        with profile_path.open("r", encoding="utf-8") as handle:
            profile = analyze_profile(json.load(handle), requested_name)
        if not profile["requested_provider_used"]:
            raise BenchmarkError(
                f"Requested provider {requested_name} did not execute any profiled node; used {profile['node_providers']}"
            )
        if profile["cpu_fallback"]:
            raise BenchmarkError(
                f"Requested provider {requested_name} partially fell back to CPU; used {profile['node_providers']}"
            )

        device_description = None
        if selected_device is not None:
            all_devices = describe_ep_devices(ort)
            matching = [d for d in all_devices if d["ep_name"] == requested_name]
            device_description = matching[args.device_index]

        return {
            "schema": "windows_onnx_gpu.benchmark.v1",
            "kind": "benchmark",
            "timestamp_utc": utc_now(),
            "status": "pass",
            "provider_requested": requested_name,
            "provider_real": profile["node_providers"],
            "provider_device_index": None if args.provider == "cpu" else args.device_index,
            "device": device_description,
            "session_providers": session_providers,
            "cpu_fallback": profile["cpu_fallback"],
            "profile": profile,
            "model": {
                "path": str(MODEL.relative_to(ROOT)),
                "sha256": model_sha256,
                "input_shape": [64, 64],
                "max_abs_error": max_abs_error,
            },
            "warmup_iterations": args.warmup,
            "measured_iterations": args.iterations,
            "initialization_ms": init_ms,
            "latency_ms": {
                "mean": sum(latencies) / len(latencies),
                "p50": percentile(latencies, 0.50),
                "p90": percentile(latencies, 0.90),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "min": min(latencies),
                "max": max(latencies),
            },
            "throughput_inferences_per_second": args.iterations / elapsed,
            "process_id": os.getpid(),
            "ort_version": ort.__version__,
        }
    finally:
        if profile_path and profile_path.exists():
            profile_path.unlink(missing_ok=True)
        unregister(ort, registered)
        if catalog:
            catalog.close()


def run_command(args: argparse.Namespace) -> int:
    record = run_once(args)
    append_jsonl(args.output, record)
    return 0


def dual(args: argparse.Namespace) -> int:
    if len(args.device_index) != 2 or args.device_index[0] == args.device_index[1]:
        raise BenchmarkError("dual requires exactly two different --device-index values")
    with tempfile.TemporaryDirectory(prefix="windows-onnx-gpu-dual-") as temp_dir:
        processes = []
        child_paths = []
        for slot, device_index in enumerate(args.device_index):
            child_path = Path(temp_dir) / f"child-{slot}.jsonl"
            child_paths.append(child_path)
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "run",
                "--provider",
                args.provider,
                "--device-index",
                str(device_index),
                "--warmup",
                str(args.warmup),
                "--iterations",
                str(args.iterations),
                "--output",
                str(child_path),
            ]
            processes.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))

        children = []
        errors = []
        for device_index, process, child_path in zip(args.device_index, processes, child_paths):
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                errors.append({"device_index": device_index, "exit_code": process.returncode, "stderr": stderr.strip(), "stdout": stdout.strip()})
            elif child_path.exists():
                children.append(json.loads(child_path.read_text(encoding="utf-8").splitlines()[-1]))
            else:
                errors.append({"device_index": device_index, "exit_code": process.returncode, "stderr": "child report missing", "stdout": stdout.strip()})

        record = {
            "schema": "windows_onnx_gpu.dual.v1",
            "kind": "dual_benchmark",
            "timestamp_utc": utc_now(),
            "status": "pass" if not errors and len(children) == 2 else "fail",
            "provider_requested": PROVIDER_NAMES[args.provider],
            "device_indexes": args.device_index,
            "independent_processes": True,
            "runs": children,
            "errors": errors,
        }
        append_jsonl(args.output, record)
        if record["status"] != "pass":
            raise BenchmarkError(f"one or more dual benchmark workers failed: {errors}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Strict native Windows ONNX GPU benchmark")
    subparsers = result.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inspect", help="enumerate providers and physical devices")
    inventory.add_argument("--prepare-provider", choices=("migraphx", "directml"))
    inventory.add_argument("--output", type=Path)
    inventory.set_defaults(func=inspect)

    run = subparsers.add_parser("run", help="benchmark one provider/device")
    run.add_argument("--provider", required=True, choices=tuple(PROVIDER_NAMES))
    run.add_argument("--device-index", type=int, default=0)
    run.add_argument("--warmup", type=int, default=10)
    run.add_argument("--iterations", type=int, default=100)
    run.add_argument("--output", type=Path)
    run.set_defaults(func=run_command)

    parallel = subparsers.add_parser("dual", help="benchmark two GPUs in independent processes")
    parallel.add_argument("--provider", required=True, choices=("migraphx", "directml"))
    parallel.add_argument("--device-index", type=int, action="append", required=True)
    parallel.add_argument("--warmup", type=int, default=10)
    parallel.add_argument("--iterations", type=int, default=100)
    parallel.add_argument("--output", type=Path)
    parallel.set_defaults(func=dual)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if getattr(args, "warmup", 1) < 0 or getattr(args, "iterations", 1) < 1:
            raise BenchmarkError("warmup must be >= 0 and iterations must be >= 1")
        return args.func(args)
    except BenchmarkError as exc:
        error = {
            "schema": "windows_onnx_gpu.error.v1",
            "kind": "error",
            "timestamp_utc": utc_now(),
            "status": "fail",
            "error": str(exc),
        }
        append_jsonl(getattr(args, "output", None), error)
        return 2
    except Exception as exc:
        error = {
            "schema": "windows_onnx_gpu.error.v1",
            "kind": "error",
            "timestamp_utc": utc_now(),
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }
        append_jsonl(getattr(args, "output", None), error)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
