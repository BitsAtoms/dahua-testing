"""Health of the system parts for mini_left (the data part of roadmap step 4.2).

Computed here, from sources the screens service can already read, so the
rules can be tuned without restarting the supervisor:

- service states from the supervisor's `stack_status` lines (every 10 s) and
  restarts from its `exited code=` lines;
- Frigate and its object detector from Frigate's `/api/stats`;
- the MQTT broker by opening a connection;
- transport delay from the receiver database (read only).

The Frigate detector signals were observed on 2026-09-30
(experiments/frigate-zmq-detector): blind (zmq detector answering instantly
with nothing), stalled (cameras skipping most frames) and broken (absurd or
frozen inference time).
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from typing import Any, Callable
import urllib.request


STATE_ORDER = {"ok": 0, "unknown": 1, "warning": 2, "error": 3, "critical": 4}
INFRASTRUCTURE = {"mqtt", "frigate", "frigate_gpu_detector"}
SERVICE_NAMES = {
    "track_receiver": "receptor",
    "batcomputer_ui": "pantallas",
    "tracking_engine": "seguimiento",
    "dahua_dashboard": "colector Dahua",
    "frigate_adapter": "conexión con Frigate",
    "detector_consensus": "verificador",
    "visual_reid": "análisis visual",
}

SUPERVISOR_SILENCE_SECONDS = 30
RESTART_WINDOW_SECONDS = 600
RESTARTS_WARNING = 3
BLIND_BELOW_MS = 1.0
BROKEN_ABOVE_MS = 1000.0
FROZEN_SECONDS = 300
STALLED_SECONDS = 90
DELAY_WINDOW_SECONDS = 300
DELAY_WARNING_MS = 2000.0
CONFIG_REFRESH_SECONDS = 60

_FIELDS = re.compile(r"(\w+)=(\S+)")
_EXITED = re.compile(r"^\[(\w+)\] exited code=")


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    state: str
    value: str


def parse_time(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp() if value else None
    except ValueError:
        return None


class SupervisorWatch:
    """Fed with every raw supervisor log line (a SupervisorConsole observer)."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.exits: deque[tuple[float, str]] = deque(maxlen=500)
        self.last_at: float | None = None
        self.lock = threading.Lock()

    def observe(self, at: str, text: str) -> None:
        moment = parse_time(at)
        with self.lock:
            if moment is not None:
                self.last_at = moment
            if text.startswith("stack_status"):
                self.states = dict(_FIELDS.findall(text))
            match = _EXITED.match(text)
            if match and moment is not None:
                self.exits.append((moment, match.group(1)))

    def view(self) -> tuple[dict[str, str], list[tuple[float, str]], float | None]:
        with self.lock:
            return dict(self.states), list(self.exits), self.last_at


def frigate_json(endpoint: str, base: str = "http://127.0.0.1:5000/api") -> dict | None:
    try:
        with urllib.request.urlopen(f"{base}/{endpoint}", timeout=2) as response:
            document = json.load(response)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def receiver_delays(database: Path, since: float) -> list[float]:
    """Milliseconds from publication to reception of recent track updates."""
    if not database.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                "SELECT published_at, receiver_received_at FROM track_updates "
                "WHERE receiver_received_us >= ? LIMIT 20000",
                (int(since * 1_000_000),),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    delays = []
    for published, received in rows:
        start, end = parse_time(published), parse_time(received)
        if start is not None and end is not None:
            delays.append(max(0.0, (end - start) * 1000))
    return delays


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


class HealthMonitor:
    def __init__(
        self,
        watch: SupervisorWatch,
        frigate: Callable[[str], dict | None] = frigate_json,
        tcp: Callable[[str, int], bool] = tcp_open,
        delays: Callable[[float], list[float]] = lambda since: [],
        clock: Callable[[], float] = time.time,
        cache_seconds: float = 2.0,
        refresh: Callable[[], None] = lambda: None,
    ) -> None:
        self.watch = watch
        # Reads the supervisor log, so the watch is current even when no
        # screen is showing the console.
        self.refresh = refresh
        self.frigate = frigate
        self.tcp = tcp
        self.delays = delays
        self.clock = clock
        self.cache_seconds = cache_seconds
        self.cached: dict | None = None
        self.cached_at = 0.0
        self.detector_types: list[str] | None = None
        self.config_at: float | None = None
        self.speed: float | None = None
        self.speed_since: float | None = None
        self.stalled_since: dict[str, float] = {}
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if self.cached is None or now - self.cached_at >= self.cache_seconds:
                self.refresh()
                self.cached = self._evaluate(now)
                self.cached_at = now
            return self.cached

    def _evaluate(self, now: float) -> dict[str, Any]:
        states, exits, last_at = self.watch.view()
        stats = self.frigate("stats")
        mqtt_up = self.tcp("127.0.0.1", 1883)
        items = [
            self._containers(mqtt_up, stats is not None),
            Item("mqtt", "Mensajería", "ok" if mqtt_up else "critical", "conectada" if mqtt_up else "sin conexión"),
            self._frigate(stats),
            self._detector(now, stats, states),
            self._services(now, states, exits, last_at),
            self._delay(now),
        ]
        worst = max(items, key=lambda item: STATE_ORDER[item.state]).state
        counts = {state: sum(item.state == state for item in items) for state in STATE_ORDER}
        return {
            "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "overall": worst,
            "counts": counts,
            "items": [asdict(item) for item in items],
        }

    @staticmethod
    def _containers(mqtt_up: bool, frigate_up: bool) -> Item:
        # Mosquitto and Frigate both run in Docker: when neither answers, Docker is down.
        if mqtt_up or frigate_up:
            return Item("docker", "Contenedores", "ok", "en marcha")
        return Item("docker", "Contenedores", "critical", "parados")

    @staticmethod
    def _frigate(stats: dict | None) -> Item:
        if stats is None:
            return Item("frigate", "Análisis de vídeo", "error", "no responde")
        cameras = [c for c in stats.get("cameras", {}).values() if c.get("detection_enabled")]
        watching = sum((c.get("camera_fps") or 0) > 0 for c in cameras)
        return Item("frigate", "Análisis de vídeo", "ok",
                    f"{watching} cámara{'s' if watching != 1 else ''} analizándose")

    def _detector(self, now: float, stats: dict | None, states: dict[str, str]) -> Item:
        uses_gpu = self._uses_gpu(now)
        label = "Detector de IA" + (" · GPU" if uses_gpu else " · CPU" if uses_gpu is False else "")
        if stats is None:
            return Item("detector", label, "unknown", "sin datos")
        if uses_gpu and states.get("frigate_gpu_detector", "running") != "running":
            return Item("detector", label, "error", "programa de la GPU detenido")
        speeds = [d.get("inference_speed") for d in stats.get("detectors", {}).values()]
        speeds = [float(s) for s in speeds if isinstance(s, (int, float))]
        if not speeds:
            return Item("detector", label, "unknown", "sin datos")
        speed = max(speeds)
        active = sum(c.get("detection_fps") or 0 for c in stats.get("cameras", {}).values()) > 0
        if speed != self.speed or not active:
            self.speed, self.speed_since = speed, now
        stalled = self._stalled_cameras(now, stats)
        if uses_gpu and speed < BLIND_BELOW_MS:
            return Item("detector", label, "error", "ciego: no ve a nadie")
        if speed > BROKEN_ABOVE_MS:
            return Item("detector", label, "error", f"roto: {speed / 1000:.1f} s por imagen")
        if active and self.speed_since is not None and now - self.speed_since >= FROZEN_SECONDS:
            return Item("detector", label, "error", "congelado")
        if stalled:
            return Item("detector", label, "error", f"atascado en {stalled} cámara{'s' if stalled > 1 else ''}")
        return Item("detector", label, "ok", f"{speed:.0f} ms por imagen")

    def _stalled_cameras(self, now: float, stats: dict) -> int:
        stalled = 0
        for name, camera in stats.get("cameras", {}).items():
            fps = camera.get("camera_fps") or 0
            jammed = (camera.get("detection_enabled") and fps > 0
                      and (camera.get("process_fps") or 0) < 0.5 * fps
                      and (camera.get("skipped_fps") or 0) > 0.5 * fps)
            if not jammed:
                self.stalled_since.pop(name, None)
                continue
            since = self.stalled_since.setdefault(name, now)
            # After every Frigate restart cameras skip frames for about a minute.
            if now - since >= STALLED_SECONDS:
                stalled += 1
        return stalled

    def _uses_gpu(self, now: float) -> bool | None:
        if self.config_at is None or now - self.config_at >= CONFIG_REFRESH_SECONDS:
            config = self.frigate("config")
            if config is not None:
                self.detector_types = [str(d.get("type")) for d in (config.get("detectors") or {}).values()]
                self.config_at = now
        if self.detector_types is None:
            return None
        return "zmq" in self.detector_types

    @staticmethod
    def _services(now: float, states: dict[str, str], exits: list[tuple[float, str]],
                  last_at: float | None) -> Item:
        label = "Servicios del sistema"
        if last_at is None or now - last_at > SUPERVISOR_SILENCE_SECONDS:
            return Item("services", label, "critical", "el supervisor no responde")
        own = {name: state for name, state in states.items() if name not in INFRASTRUCTURE}
        if not own:
            return Item("services", label, "unknown", "arrancando")
        stopped = [name for name, state in own.items() if state != "running"]
        restarts = sum(1 for moment, _ in exits if now - moment <= RESTART_WINDOW_SECONDS)
        if stopped:
            names = ", ".join(SERVICE_NAMES.get(name, name) for name in stopped[:2])
            more = f" y {len(stopped) - 2} más" if len(stopped) > 2 else ""
            return Item("services", label, "error", f"detenido: {names}{more}")
        if restarts > RESTARTS_WARNING:
            return Item("services", label, "warning", f"{restarts} reinicios en 10 min")
        return Item("services", label, "ok",
                    f"{len(own)} de {len(own)} en marcha" + (f" · {restarts} reinicios" if restarts else ""))

    def _delay(self, now: float) -> Item:
        values = self.delays(now - DELAY_WINDOW_SECONDS)
        label = "Retraso de los datos"
        if not values:
            # No people, no messages: not a fault.
            return Item("delay", label, "unknown", "sin datos recientes")
        worst = p95(values)
        text = f"{worst:.0f} ms" if worst < 1000 else f"{worst / 1000:.1f} s"
        return Item("delay", label, "warning" if worst > DELAY_WARNING_MS else "ok", text)
