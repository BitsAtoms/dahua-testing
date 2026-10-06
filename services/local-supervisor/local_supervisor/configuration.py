"""Validated, fixed-command configuration for the local stack."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any


SERVICE_NAMES = (
    "track_receiver",
    "batcomputer_ui",
    "tracking_engine",
    "dahua_dashboard",
    "frigate_adapter",
    "detector_consensus",
    "visual_reid",
)

# Services that no longer exist: accepted in local-stack.json and ignored, so
# an older local file does not stop the system (space_mapper was replaced by
# the Batcomputer screens on 2026-10-06).
RETIRED_SERVICES = ("space_mapper",)

FRIGATE_DETECTORS = ("cpu", "gpu")
GPU_DETECTOR_SERVICE = "frigate_gpu_detector"
# Frigate reaches it at tcp://host.docker.internal:5555; loopback only.
GPU_DETECTOR_ENDPOINT = ("127.0.0.1", 5555)
COMPOSE_FILE = "deploy/docker/compose.yml"


@dataclass(frozen=True)
class StackConfig:
    enabled: dict[str, bool]
    env_file: Path
    log_root: Path
    visual_device: str
    status_seconds: float
    startup_delay_seconds: float
    docker_stack: bool = True
    frigate_detector: str = "cpu"
    docker_start_timeout_seconds: float = 240.0
    compose_file: Path = Path(COMPOSE_FILE)


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
        "docker_stack",
        "frigate_detector",
        "docker_start_timeout_seconds",
    }
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"unknown local stack settings: {', '.join(unknown)}")
    if document.get("schema_version", "local_stack.v1") != "local_stack.v1":
        raise ValueError("unsupported local stack schema_version")

    configured_services = document.get("services", {})
    if not isinstance(configured_services, dict):
        raise ValueError("services must be a JSON object")
    unknown_services = sorted(set(configured_services) - set(SERVICE_NAMES) - set(RETIRED_SERVICES))
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
    docker_stack = _as_bool(document.get("docker_stack", True), "docker_stack")
    frigate_detector = document.get("frigate_detector", "cpu")
    if frigate_detector not in FRIGATE_DETECTORS:
        raise ValueError(f"frigate_detector must be one of: {', '.join(FRIGATE_DETECTORS)}")
    if frigate_detector == "gpu" and not docker_stack:
        # Frigate must be restarted after the detector; only the Docker stack can.
        raise ValueError("frigate_detector gpu requires docker_stack true")
    docker_start_timeout_seconds = float(document.get("docker_start_timeout_seconds", 240.0))
    if docker_start_timeout_seconds <= 0:
        raise ValueError("docker_start_timeout_seconds must be positive")
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
        docker_stack=docker_stack,
        frigate_detector=frigate_detector,
        docker_start_timeout_seconds=docker_start_timeout_seconds,
        compose_file=(repository_root / COMPOSE_FILE).resolve(),
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
        "batcomputer_ui": ServiceSpec(
            "batcomputer_ui",
            (str(system_python), "-u", script("services/batcomputer-ui/server.py")),
            listen_endpoints=(("127.0.0.1", 8092),),
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
        "detector_consensus": ServiceSpec(
            "detector_consensus",
            (
                str(visual_python.resolve()),
                "-u",
                script("experiments/visual-reid/detector_consensus_service.py"),
                "--device",
                config.visual_device,
            ),
            (
                visual_python,
                repository_root
                / "experiments/visual-reid/models/detectors/yolox_tiny.onnx",
                repository_root / "experiments/visual-reid/adaptive-capture.local.json",
                config.env_file,
            ),
            ("cv2", "openvino"),
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
                / "experiments/visual-reid/models/facenet-small-v1/facenet.tflite",
                repository_root
                / "experiments/visual-reid/models/landmarks-regression-retail-0009/FP16/landmarks-regression-retail-0009.xml",
            ),
            ("cv2", "openvino"),
        ),
    }
    return [definitions[name] for name in SERVICE_NAMES if config.enabled[name]]


def build_gpu_detector_spec(config: StackConfig, repository_root: Path) -> ServiceSpec | None:
    """The Frigate zmq detector client, only when Frigate detects on the GPU."""
    if config.frigate_detector != "gpu":
        return None
    root = repository_root / "experiments/frigate-zmq-detector"
    python = root / ".venv/Scripts/python.exe"
    if sys.platform != "win32":
        python = root / ".venv/bin/python"
    client = root / "vendor/detector/zmq_onnx_client.py"
    host, port = GPU_DETECTOR_ENDPOINT
    return ServiceSpec(
        GPU_DETECTOR_SERVICE,
        (
            str(python.resolve()),
            "-u",
            str(client.resolve()),
            "--endpoint",
            f"tcp://{host}:{port}",
            "--model",
            "AUTO",
            # DirectML only, so a GPU failure is loud instead of a CPU fallback.
            "--providers",
            "DmlExecutionProvider",
        ),
        (python, client),
        ("onnxruntime", "zmq"),
        listen_endpoints=(GPU_DETECTOR_ENDPOINT,),
    )


def _resolve(root: Path, value: object) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value
