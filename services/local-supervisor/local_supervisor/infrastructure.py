"""Ordered start of Docker Desktop, Mosquitto, the GPU detector and Frigate.

Frigate's zmq detector checks its model only when Frigate starts. If Frigate
starts before the detector client, or the client restarts, Frigate stays
blind or stalled until it is restarted (experiments/frigate-zmq-detector).
Docker starts Frigate together with the engine, so in GPU mode Frigate is
restarted once the client listens.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Protocol
import urllib.request

from .docker_stack import DockerCommandError


MQTT_ENDPOINT = ("127.0.0.1", 1883)
FRIGATE_API = "http://127.0.0.1:5000/api"
FRIGATE_CONTAINER = "frigate"


class StartupError(RuntimeError):
    """A required piece did not become healthy in time."""


class Detector(Protocol):
    """The part of ManagedService used for the GPU detector client."""

    def start(self) -> None: ...

    def stop(self, timeout: float = 20.0) -> None: ...

    def maybe_restart(self) -> bool: ...

    def state(self) -> str: ...


class Docker(Protocol):
    def engine_ready(self) -> bool: ...

    def start_desktop(self, timeout_seconds: float) -> None: ...

    def compose_up(self, *services: str) -> None: ...

    def compose_stop(self, *services: str) -> None: ...

    def started_at(self, container: str) -> str | None: ...

    def restart(self, container: str) -> None: ...

    def begin_restart(self, container: str) -> subprocess.Popen: ...


@dataclass(frozen=True)
class Timeouts:
    engine: float = 240.0
    mqtt: float = 60.0
    detector: float = 60.0
    frigate: float = 180.0
    poll_interval: float = 1.0
    restart_retry: float = 30.0


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(url: str, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


class Infrastructure:
    def __init__(
        self,
        docker: Docker,
        detector: Detector | None,
        detector_endpoint: tuple[str, int],
        console: Callable[[str], None],
        timeouts: Timeouts = Timeouts(),
        tcp: Callable[[str, int], bool] = tcp_open,
        fetch_json: Callable[[str], Any] = http_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.docker = docker
        self.detector = detector
        self.detector_endpoint = detector_endpoint
        self.console = console
        self.timeouts = timeouts
        self.tcp = tcp
        self.fetch_json = fetch_json
        self.clock = clock
        self.engine_seen = False
        self.frigate_restart_needed = False
        self.frigate_restart: subprocess.Popen | None = None
        self.next_restart_at = 0.0

    @property
    def expected_detector(self) -> str:
        return "gpu" if self.detector is not None else "cpu"

    def start(self, stop: threading.Event) -> None:
        self._start_engine(stop)
        self.console("mqtt action=compose_up")
        self._docker(self.docker.compose_up, "mqtt")
        self._wait("mqtt", lambda: self.tcp(*MQTT_ENDPOINT), self.timeouts.mqtt, stop)
        if self.detector is not None:
            self.detector.start()
            self._wait(
                "frigate_gpu_detector",
                lambda: self.tcp(*self.detector_endpoint),
                self.timeouts.detector,
                stop,
                failed=lambda: self.detector.state().startswith("exited"),
            )
        self._start_frigate(stop)

    def supervise(self) -> None:
        """Restart Frigate after the detector client restarts. Non-blocking."""
        if self.detector is None:
            return
        if self.detector.maybe_restart():
            self.frigate_restart_needed = True
            self.console("frigate restart_pending reason=detector_restarted")
        if self.frigate_restart is not None:
            code = self.frigate_restart.poll()
            if code is None:
                return
            self.frigate_restart = None
            if code == 0:
                self.console("frigate restarted reason=detector_restarted")
            else:
                self.console(
                    f"ERROR frigate_restart_failed code={code} "
                    f"retry_in={self.timeouts.restart_retry:g}s"
                )
                self.frigate_restart_needed = True
                self.next_restart_at = self.clock() + self.timeouts.restart_retry
        if (
            self.frigate_restart_needed
            and self.clock() >= self.next_restart_at
            and self.tcp(*self.detector_endpoint)
        ):
            self.frigate_restart_needed = False
            self.console("frigate action=restart reason=detector_restarted")
            self.frigate_restart = self.docker.begin_restart(FRIGATE_CONTAINER)

    def stop(self) -> None:
        """Stop Frigate and the detector client; Mosquitto and Docker stay up."""
        if self.frigate_restart is not None:
            try:
                self.frigate_restart.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.frigate_restart.kill()
            self.frigate_restart = None
        if self.engine_seen:
            self.console("frigate action=compose_stop")
            try:
                self.docker.compose_stop(FRIGATE_CONTAINER)
                self.console("frigate stopped")
            except DockerCommandError as error:
                self.console(f"ERROR frigate_stop_failed {error}")
        if self.detector is not None:
            self.detector.stop()

    def state(self) -> str:
        parts = [f"mqtt={'up' if self.tcp(*MQTT_ENDPOINT) else 'down'}"]
        if self.detector is not None:
            parts.append(f"frigate_gpu_detector={self.detector.state()}")
        if self.frigate_restart is not None or self.frigate_restart_needed:
            frigate = "restarting"
        else:
            frigate = "up" if self._frigate_json("stats") is not None else "down"
        parts.append(f"frigate={frigate}")
        return " ".join(parts)

    def _start_engine(self, stop: threading.Event) -> None:
        started = self.clock()
        if not self.docker.engine_ready():
            self.console(f"docker action=start_desktop timeout={self.timeouts.engine:g}s")
            self._docker(self.docker.start_desktop, self.timeouts.engine)
            self._wait("docker", self.docker.engine_ready, self.timeouts.engine, stop)
        self.engine_seen = True
        self.console(f"docker engine=ready after={self.clock() - started:.1f}s")

    def _start_frigate(self, stop: threading.Event) -> None:
        before = self.docker.started_at(FRIGATE_CONTAINER)
        self._docker(self.docker.compose_up, FRIGATE_CONTAINER)
        after = self.docker.started_at(FRIGATE_CONTAINER)
        if before is not None and after == before:
            if self.detector is not None:
                # Docker started it with the engine, before the detector client.
                self.console("frigate action=restart reason=started_before_detector")
                self._docker(self.docker.restart, FRIGATE_CONTAINER)
            else:
                self.console("frigate action=none reason=already_running")
        else:
            self.console("frigate action=started")
        config: dict[str, Any] = {}

        def api_ready() -> bool:
            loaded = self._frigate_json("config")
            if loaded is None:
                return False
            config.update(loaded)
            return True

        self._wait("frigate", api_ready, self.timeouts.frigate, stop)
        self._check_detector_type(config)

    def _check_detector_type(self, config: dict[str, Any]) -> None:
        detectors = config.get("detectors") or {}
        types = sorted({str(value.get("type")) for value in detectors.values()})
        uses_gpu = "zmq" in types
        frigate = ",".join(types) or "none"
        if uses_gpu != (self.detector is not None):
            # Frigate on zmq without the client is blind; on CPU the client is idle.
            self.console(
                f"ERROR frigate_detector_mismatch supervisor={self.expected_detector} "
                f"frigate={frigate}"
            )
        else:
            self.console(f"frigate detector={frigate} expected={self.expected_detector}")

    def _frigate_json(self, endpoint: str) -> dict[str, Any] | None:
        try:
            loaded = self.fetch_json(f"{FRIGATE_API}/{endpoint}")
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _docker(self, action: Callable[..., None], *arguments: Any) -> None:
        try:
            action(*arguments)
        except DockerCommandError as error:
            raise StartupError(str(error)) from error

    def _wait(
        self,
        name: str,
        ready: Callable[[], bool],
        timeout: float,
        stop: threading.Event,
        failed: Callable[[], bool] | None = None,
    ) -> None:
        started = self.clock()
        deadline = started + timeout
        while True:
            if stop.is_set():
                raise StartupError(f"{name} stop requested during startup")
            if ready():
                self.console(f"{name} ready after={self.clock() - started:.1f}s")
                return
            if failed is not None and failed():
                raise StartupError(f"{name} exited during startup")
            if self.clock() >= deadline:
                raise StartupError(f"{name} not ready after {timeout:g}s")
            stop.wait(self.timeouts.poll_interval)
