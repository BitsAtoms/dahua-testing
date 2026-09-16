"""Validated, fixed-command configuration for the local stack."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any


SERVICE_NAMES = (
    "track_receiver",
    "space_mapper",
    "tracking_engine",
    "dahua_dashboard",
    "frigate_adapter",
    "visual_reid",
)


@dataclass(frozen=True)
class StackConfig:
    enabled: dict[str, bool]
    env_file: Path
    log_root: Path
    visual_device: str
    status_seconds: float
    startup_delay_seconds: float


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: tuple[str, ...]
    required_paths: tuple[Path, ...] = ()
    required_imports: tuple[str, ...] = ()
    listen_endpoints: tuple[tuple[str, int], ...] = ()


def load_config(path: Path, repository_root: Path) -> StackConfig:
    document: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("local stack configuration must be a JSON object")
        document = loaded
    elif path.name != "local-stack.json":
        raise FileNotFoundError(f"local stack configuration does not exist: {path}")

    allowed = {
        "schema_version",
        "services",
        "env_file",
        "log_root",
        "visual_device",
        "status_seconds",
        "startup_delay_seconds",
    }
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"unknown local stack settings: {', '.join(unknown)}")
    if document.get("schema_version", "local_stack.v1") != "local_stack.v1":
        raise ValueError("unsupported local stack schema_version")

    configured_services = document.get("services", {})
    if not isinstance(configured_services, dict):
        raise ValueError("services must be a JSON object")
    unknown_services = sorted(set(configured_services) - set(SERVICE_NAMES))
    if unknown_services:
        raise ValueError(f"unknown services: {', '.join(unknown_services)}")
    enabled = {
        name: _as_bool(configured_services.get(name, True), f"services.{name}")
        for name in SERVICE_NAMES
    }
    status_seconds = float(document.get("status_seconds", 10.0))
    startup_delay_seconds = float(document.get("startup_delay_seconds", 0.4))
    if status_seconds <= 0 or startup_delay_seconds < 0:
        raise ValueError("status_seconds must be positive and startup delay non-negative")
    visual_device = str(document.get("visual_device", "CPU")).strip()
    if not visual_device:
        raise ValueError("visual_device cannot be empty")
    return StackConfig(
        enabled=enabled,
        env_file=_resolve(repository_root, document.get("env_file", ".env")),
        log_root=_resolve(
            repository_root,
            document.get("log_root", "runtime/local-supervisor/logs"),
        ),
        visual_device=visual_device,
        status_seconds=status_seconds,
        startup_delay_seconds=startup_delay_seconds,
    )


def build_specs(config: StackConfig, repository_root: Path) -> list[ServiceSpec]:
    system_python = Path(sys.executable).resolve()
    visual_python = repository_root / "experiments/visual-reid/.venv/Scripts/python.exe"
    if sys.platform != "win32":
        visual_python = repository_root / "experiments/visual-reid/.venv/bin/python"

    def script(relative: str) -> str:
        return str((repository_root / relative).resolve())

    definitions = {
        "track_receiver": ServiceSpec(
            "track_receiver",
            (
                str(system_python),
                "-u",
                script("services/track-receiver/mqtt_service.py"),
                "--env-file",
                str(config.env_file),
            ),
            (config.env_file,),
            ("paho.mqtt.client",),
        ),
        "space_mapper": ServiceSpec(
            "space_mapper",
            (str(system_python), "-u", script("services/space-mapper/server.py")),
            listen_endpoints=(("127.0.0.1", 8091),),
        ),
        "tracking_engine": ServiceSpec(
            "tracking_engine",
            (str(system_python), "-u", script("services/tracking-engine/live_service.py")),
        ),
        "dahua_dashboard": ServiceSpec(
            "dahua_dashboard",
            (
                str(system_python),
                "-u",
                script("experiments/dahua-netsdk/collector/dashboard.py"),
                "--env-file",
                str(config.env_file),
            ),
            (
                config.env_file,
                repository_root / "experiments/dahua-netsdk/cameras.local.json",
                repository_root / "experiments/dahua-netsdk/build/dahua-events.exe",
            ),
            listen_endpoints=(("127.0.0.1", 8090),),
        ),
        "frigate_adapter": ServiceSpec(
            "frigate_adapter",
            (
                str(system_python),
                "-u",
                script("experiments/frigate-adapter/mqtt_runner.py"),
                "--env-file",
                str(config.env_file),
            ),
            (config.env_file,),
            ("paho.mqtt.client",),
        ),
        "visual_reid": ServiceSpec(
            "visual_reid",
            (
                str(visual_python.resolve()),
                "-u",
                script("experiments/visual-reid/live_service.py"),
                "--device",
                config.visual_device,
            ),
            (
                visual_python,
                repository_root
                / "experiments/visual-reid/models/person-reidentification-retail-0287/FP16/person-reidentification-retail-0287.xml",
                repository_root
                / "experiments/visual-reid/models/face-reidentification-retail-0095/FP16/face-reidentification-retail-0095.xml",
                repository_root
                / "experiments/visual-reid/models/landmarks-regression-retail-0009/FP16/landmarks-regression-retail-0009.xml",
            ),
            ("cv2", "openvino"),
        ),
    }
    return [definitions[name] for name in SERVICE_NAMES if config.enabled[name]]


def _resolve(root: Path, value: object) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value
