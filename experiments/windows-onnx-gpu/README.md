# Native Windows ONNX GPU benchmark

This experiment is the deployment gate for the final Windows/AMD computer. It
does not connect to cameras, Frigate, MQTT or the tracking services. It proves
only that ONNX inference can be assigned to each physical GPU and to two GPUs
at once using independent native Windows processes.

The benchmark prefers Windows ML's AMD `MIGraphXExecutionProvider`. The
included `DmlExecutionProvider` is the fallback. CPU is a correctness baseline.
No result is accepted merely because a session was created: the ONNX Runtime
profile must show that the requested provider executed the graph, and any CPU
node in a GPU run makes that run fail.

## Why Python

Python is the smallest viable implementation now that the official Windows ML
packages expose all APIs needed by this gate:

- the Windows ML execution-provider catalog can discover and install MIGraphX;
- ONNX Runtime exposes `get_ep_devices()` and
  `SessionOptions.add_provider_for_devices()` for explicit device selection;
- ONNX Runtime profiling records the provider that executed each node.

This avoids a Visual Studio/C++ build without giving up explicit adapter
selection. Python must be a normal x64 installation from python.org or winget,
not the Microsoft Store build. Use Python 3.12 for the pinned environment.

## Prerequisites on the AMD computer

1. Install all Windows updates and the current AMD driver. Windows ML catalog
   providers require Windows 11 24H2 build 26100 or newer. MIGraphX requires an
   AMD RDNA 3-or-newer GPU and AMD driver `25.10.13.09` or newer.
2. Install 64-bit Python 3.12:

   ```powershell
   winget install --exact --id Python.Python.3.12
   ```

3. From Microsoft's [Windows App SDK downloads](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads),
   download and run the x64 installer for stable Windows App SDK 2.3.1. This
   matches the pinned 2.3 Python bindings. A newer compatible 2.x runtime may
   already be installed, but the benchmark inventory will expose setup errors
   rather than silently continuing.
4. Clone this repository and check out `codex/windows-onnx-shadow-provider`.

The pinned dependencies are the official packages named by Microsoft's Python
installation documentation. Vendor providers such as MIGraphX are not bundled;
Windows downloads them when `EnsureReadyAsync()` is called. That first call may
take several minutes and requires Windows Update/component-download access.
`onnxruntime-windowsml` is pinned to the exact version required by the pinned
Windows ML 2.3.0 Python bindings; these two packages must be upgraded together.
The optional `[all]` binding extra is intentionally omitted because it expands
unrelated Windows namespaces that this console benchmark never imports. The
three WinRT namespaces imported directly by the generated Windows ML binding
are pinned individually instead.

## Prepare the environment

Run from the repository root:

```powershell
.\experiments\windows-onnx-gpu\bootstrap.ps1
```

The script creates the ignored `.venv`, installs the pinned Windows ML/ORT
packages and prints an initial inventory. It does not download a detector or
any camera data. The committed model is a 17 KB synthetic MatMul/Add/Relu ONNX
fixture (opset 20); its output is checked against NumPy on every run.
The committed `model-manifest.json` also pins its size and SHA-256, and the
benchmark refuses to run if the fixture does not match it.

For CPU-only development and tests:

```powershell
.\experiments\windows-onnx-gpu\bootstrap.ps1 -CpuOnly
.\experiments\windows-onnx-gpu\.venv\Scripts\python.exe -m unittest discover -s tests\windows-onnx-gpu -v
```

## Inspect providers and devices

Prepare MIGraphX, then print Windows, driver, physical GPU, Windows ML catalog
and ORT device data:

```powershell
.\experiments\windows-onnx-gpu\.venv\Scripts\python.exe .\experiments\windows-onnx-gpu\benchmark.py inspect --prepare-provider migraphx
```

Each ORT device has both `ep_device_index` and a provider-local
`provider_device_index`. The benchmark commands use the latter. Physical GPU
rows include PNP ID and PCI location when Windows exposes them. For two
identical RX 9070 XT cards, retain those location strings with the report and
confirm the corresponding provider-local indexes once in Task Manager by
running a longer single-device test. Do not assume GPU 0 is the first discrete
card; it can be the integrated GPU.

If MIGraphX is absent or fails preparation, inspect DirectML without downloading
a vendor provider:

```powershell
.\experiments\windows-onnx-gpu\.venv\Scripts\python.exe .\experiments\windows-onnx-gpu\benchmark.py inspect --prepare-provider directml
```

## Run the acceptance matrix

After reading the inventory, substitute the two provider-local indexes that
belong to the discrete RX 9070 XT cards:

```powershell
.\experiments\windows-onnx-gpu\run-matrix.ps1 -Provider migraphx -GpuA 0 -GpuB 1
```

If MIGraphX is unavailable, run the same matrix through DirectML:

```powershell
.\experiments\windows-onnx-gpu\run-matrix.ps1 -Provider directml -GpuA 0 -GpuB 1
```

The matrix runs CPU, GPU A, GPU B, then both GPUs concurrently in independent
processes. JSONL is written below the ignored
`experiments/windows-onnx-gpu/output/` directory. Send back that one report.

A GPU run exits nonzero when the requested EP is missing, the selected device
does not exist, no profiled node used the requested EP, any node falls back to
CPU, the numerical result is wrong, or a child in the dual run fails. The
report contains initialization time, mean/p50/p90/p95/p99 latency, throughput,
provider and device metadata, driver and dependency versions, and errors.

## Final Windows/AMD computer: 2026-09-28 validation

The test PC ran Windows 11 build 26200, Python 3.12.10, Windows ML bindings
2.3.0, ONNX Runtime Windows ML 1.25.2, and RX 9070 XT driver
32.0.31041.1004. The corrected readiness check compared the binding's
`ExecutionProviderReadyResultState.SUCCESS` (integer value 1), so MIGraphX
installed and registered. Windows ML supplied the AMD EP from package
`Microsoft.WinML.AMD.GPU.EP.2_1.8.64.0_x64`.

| Provider-local index | GPU | DXGI adapter | DXGI high-performance index | LUID | Video memory |
|---|---|---:|---:|---:|---:|
| 0 | RX 9070 XT | 0 | 0 | 11189931 | 16188 MB |
| 1 | RX 9070 XT | 1 | 2 | 11384819 | 16188 MB |
| 2 | Radeon integrated | 2 | 1 | 110399 | 2021 MB |

Windows also reported discrete cards on PCI buses 3 and 6, and the integrated
GPU on bus 125. The current inventory does not prove which RX LUID corresponds
to bus 3 or 6. Do not infer that mapping from the table order.

The MIGraphX matrix passed CPU and RX index 0 with all profiled GPU nodes on
`MIGraphXExecutionProvider`, then stopped on index 1. The second card failed
twice with `BatchOrCopyMLValue ... Failed to find allocator ... DeviceId:1`.
An additional bounded test with MIGraphX's documented `device_id=1` option
failed with `invalid device function`; that option was not retained in the
benchmark. No MIGraphX dual result exists. The original command output,
including the failing inventory and run, remains in the ignored
`output/benchmark-20260928T115538.jsonl`.

The DirectML fallback matrix passed CPU, each RX index separately, and two
independent GPU processes concurrently. The GPU profiles contained only
`DmlExecutionProvider` nodes; no partial or total CPU fallback was observed.
The session's available provider list also contains CPU, but this does not
mean CPU executed a node. The complete passing inventory and matrix are in
the ignored `output/benchmark-20260928T115558.jsonl`. These results validate
the small synthetic ONNX fixture only, not a detector or tracker workload.

## Source and version basis

Package/API names were checked against current official sources on 2026-09-25;
the readiness-state comparison and explicit EP-device guidance were checked
again on 2026-09-28:

- [Install and deploy Windows ML](https://learn.microsoft.com/windows/ai/new-windows-ml/distributing-your-app)
- [Install Windows ML execution providers](https://learn.microsoft.com/windows/ai/new-windows-ml/initialize-execution-providers)
- [Windows ML execution providers](https://learn.microsoft.com/windows/ai/new-windows-ml/supported-execution-providers)
- [Select execution providers](https://learn.microsoft.com/windows/ai/new-windows-ml/select-execution-providers)
- [ONNX Runtime DirectML provider](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- [ONNX Runtime MIGraphX provider options](https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html)
- [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)

DirectML is supported but in sustained engineering; Windows ML and the
hardware-vendor EP are the preferred Windows path. Neither GPU is treated as
shared memory or CrossFire: the dual command deliberately launches one process
per explicitly selected device.

The fixture was generated with `onnx==1.20.1` by `generate_fixture.py`; ONNX is
not a runtime dependency. Regenerate it only when intentionally changing the
test graph.
