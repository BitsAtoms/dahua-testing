from __future__ import annotations

import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROVIDER_NAMES = {
    "cpu": "CPUExecutionProvider",
    "migraphx": "MIGraphXExecutionProvider",
    "directml": "DmlExecutionProvider",
}


class BenchmarkError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    for method_name in ("get_key_value_pairs", "items"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                pairs = method()
                return {str(k): str(v) for k, v in dict(pairs).items()}
            except Exception:
                pass
    return {}


def describe_ep_devices(ort: Any) -> list[dict[str, Any]]:
    if not hasattr(ort, "get_ep_devices"):
        return []
    counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for global_index, ep_device in enumerate(ort.get_ep_devices()):
        ep_name = str(getattr(ep_device, "ep_name", ""))
        provider_index = counts.get(ep_name, 0)
        counts[ep_name] = provider_index + 1
        device = getattr(ep_device, "device", None)
        if device is None:
            device = getattr(ep_device, "hardware_device", None)
        device_type = getattr(device, "type", None)
        result.append(
            {
                "ep_device_index": global_index,
                "provider_device_index": provider_index,
                "ep_name": ep_name,
                "ep_vendor": str(getattr(ep_device, "ep_vendor", "")),
                "ep_metadata": _mapping(getattr(ep_device, "ep_metadata", None)),
                "ep_options": _mapping(getattr(ep_device, "ep_options", None)),
                "hardware": {
                    "type": getattr(device_type, "name", str(device_type)),
                    "vendor": str(getattr(device, "vendor", "")),
                    "vendor_id": getattr(device, "vendor_id", None),
                    "device_id": getattr(device, "device_id", None),
                    "metadata": _mapping(getattr(device, "metadata", None)),
                },
            }
        )
    return result


def _powershell_json(script: str) -> Any:
    if os.name != "nt":
        return None
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return {"error": completed.stderr.strip() or f"PowerShell exit {completed.returncode}"}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": "PowerShell returned non-JSON output", "raw": completed.stdout.strip()}


def physical_gpu_inventory() -> Any:
    script = r"""
$items = Get-CimInstance Win32_VideoController | ForEach-Object {
  $pnp = $_.PNPDeviceID
  $location = $null
  if ($pnp) {
    $location = (Get-PnpDeviceProperty -InstanceId $pnp -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction SilentlyContinue).Data
  }
  [pscustomobject]@{
    name = $_.Name
    pnp_device_id = $pnp
    adapter_compatibility = $_.AdapterCompatibility
    driver_version = $_.DriverVersion
    adapter_ram = $_.AdapterRAM
    location = $location
    status = $_.Status
  }
}
@($items) | ConvertTo-Json -Depth 4 -Compress
"""
    value = _powershell_json(script)
    if isinstance(value, dict) and "name" in value:
        return [value]
    return value or []


def package_versions(names: Iterable[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def system_info() -> dict[str, Any]:
    return {
        "timestamp_utc": utc_now(),
        "platform": platform.platform(),
        "windows_version": platform.version(),
        "windows_release": platform.release(),
        "python": sys.version,
        "python_executable": sys.executable,
        "packages": package_versions(
            (
                "numpy",
                "onnxruntime",
                "onnxruntime-windowsml",
                "wasdk-Microsoft.Windows.AI.MachineLearning",
                "wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap",
            )
        ),
        "physical_gpus": physical_gpu_inventory(),
    }


@dataclass
class WinMLCatalog:
    providers: list[dict[str, Any]]
    provider_objects: list[Any]
    bootstrap_handle: Any = None

    def close(self) -> None:
        if self.bootstrap_handle is not None:
            self.bootstrap_handle.__exit__(None, None, None)
            self.bootstrap_handle = None


def open_winml_catalog() -> WinMLCatalog:
    if os.name != "nt":
        return WinMLCatalog([], [])
    try:
        from winui3.microsoft.windows.applicationmodel.dynamicdependency.bootstrap import (
            InitializeOptions,
            initialize,
        )
        import winui3.microsoft.windows.ai.machinelearning as winml
    except ImportError as exc:
        raise BenchmarkError(
            "Windows ML Python packages are not installed; run bootstrap.ps1 without -CpuOnly"
        ) from exc

    handle = initialize(options=InitializeOptions.ON_NO_MATCH_SHOW_UI)
    handle.__enter__()
    catalog = winml.ExecutionProviderCatalog.get_default()
    objects = list(catalog.find_all_providers())
    providers = [
        {
            "name": str(provider.name),
            "ready_state": str(provider.ready_state),
            "library_path": str(provider.library_path),
        }
        for provider in objects
    ]
    return WinMLCatalog(providers, objects, handle)


def ensure_and_register_provider(ort: Any, provider_name: str) -> tuple[WinMLCatalog, list[str]]:
    catalog = open_winml_catalog()
    matches = [item for item in catalog.provider_objects if str(item.name) == provider_name]
    if not matches:
        catalog.close()
        raise BenchmarkError(f"{provider_name} is not compatible with this machine")
    registered: list[str] = []
    try:
        for provider in matches:
            result = provider.ensure_ready_async().get()
            state = str(getattr(result, "status", result))
            if "success" not in state.lower():
                raise BenchmarkError(f"Windows ML could not prepare {provider_name}: {state}")
            library_path = str(provider.library_path)
            if not library_path:
                raise BenchmarkError(f"Windows ML returned no library path for {provider_name}")
            ort.register_execution_provider_library(provider_name, library_path)
            registered.append(provider_name)
        return catalog, registered
    except Exception:
        catalog.close()
        raise


def unregister(ort: Any, names: Iterable[str]) -> None:
    for name in names:
        try:
            ort.unregister_execution_provider_library(name)
        except Exception:
            pass


def select_ep_device(ort: Any, provider_name: str, provider_device_index: int) -> Any:
    matches = [device for device in ort.get_ep_devices() if str(device.ep_name) == provider_name]
    if provider_device_index < 0 or provider_device_index >= len(matches):
        raise BenchmarkError(
            f"{provider_name} device index {provider_device_index} is unavailable; found {len(matches)} device(s)"
        )
    return matches[provider_device_index]


def analyze_profile(events: list[dict[str, Any]], requested_provider: str) -> dict[str, Any]:
    providers: set[str] = set()
    node_count = 0
    for event in events:
        args = event.get("args") or {}
        provider = args.get("provider")
        if provider:
            providers.add(str(provider))
            node_count += 1
    if not providers:
        raise BenchmarkError("ORT profile contains no node provider assignments")
    normalized_requested = requested_provider.lower().replace("directml", "dml")
    used_requested = any(
        provider.lower().replace("directml", "dml") == normalized_requested for provider in providers
    )
    cpu_fallback = requested_provider != "CPUExecutionProvider" and "CPUExecutionProvider" in providers
    return {
        "node_event_count": node_count,
        "node_providers": sorted(providers),
        "requested_provider_used": used_requested,
        "cpu_fallback": cpu_fallback,
    }


def append_jsonl(path: Path | None, record: dict[str, Any]) -> None:
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    print(line, flush=True)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
