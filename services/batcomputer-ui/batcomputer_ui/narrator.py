"""Turn the supervisor's technical log into short lines a visitor can follow.

The console on mini_center is seen by visitors (owner, 2026-10-01): it keeps
only what relates to something real happening (a camera sees a person, loses
them, photos arrive, the system starts or fails) and says it in plain Spanish.
Every line still comes from a real log line and keeps its track number, so
it reads as live data. Routine internals are dropped, and periodic summaries
are rate-limited so the screen stays readable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Callable


SERVICE_NAMES = {
    "track_receiver": "el receptor de datos",
    "batcomputer_ui": "las pantallas",
    "tracking_engine": "el motor de seguimiento",
    "dahua_dashboard": "el colector Dahua",
    "frigate_adapter": "la conexión con Frigate",
    "detector_consensus": "el verificador de detecciones",
    "visual_reid": "el análisis visual",
    "frigate_gpu_detector": "el detector de IA en la GPU",
    "mqtt": "la mensajería",
    "frigate": "Frigate",
    "docker": "Docker",
}

_FIELDS = re.compile(r"(\w+)=(\S+)")
_PREFIX = re.compile(r"^\[(\w+)\] (.*)$")


@dataclass(frozen=True)
class Story:
    at: str
    text: str
    kind: str  # event, status, system or alert


def fields(text: str) -> dict[str, str]:
    return dict(_FIELDS.findall(text))


def short_track(track: str) -> str:
    # Frigate ids look like 1790843775.15853-vsjrw5; Dahua ids are numbers.
    return "#" + track.rsplit("-", 1)[-1]


def seconds_between(start: str, end: str) -> float | None:
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def duration_text(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s" if minutes < 10 else f"{minutes} min"


class CameraNames:
    """Visitor names for cameras: the room each one counts in, from the
    building plan (space_map.v2), else the camera id."""

    def __init__(self, map_file: Path, refresh_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.map_file = map_file
        self.refresh_seconds = refresh_seconds
        self.clock = clock
        self.loaded_at: float | None = None
        self.names: dict[str, str] = {}

    def name(self, camera_id: str) -> str:
        now = self.clock()
        if self.loaded_at is None or now - self.loaded_at >= self.refresh_seconds:
            self.loaded_at = now
            self.names = self._load()
        return self.names.get(camera_id) or camera_id.replace("_", " ").title()

    def _load(self) -> dict[str, str]:
        try:
            document = json.loads(self.map_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        floors = document.get("floors", []) if isinstance(document, dict) else []
        rooms = {room.get("id"): room.get("name") for floor in floors for room in floor.get("rooms", [])}
        return {
            camera.get("camera_id"): rooms[camera.get("room_id")]
            for floor in floors
            for camera in floor.get("cameras", [])
            if rooms.get(camera.get("room_id"))
        }


class Narrator:
    def __init__(self, names: CameraNames, status_every: float = 60.0,
                 heartbeat_every: float = 300.0, handoff_every: float = 15.0) -> None:
        self.names = names
        self.status_every = status_every
        self.heartbeat_every = heartbeat_every
        self.handoff_every = handoff_every
        self.seen_since: dict[tuple[str, str], str] = {}
        # Cameras send several photos per person: tell it once per track.
        self.photographed: deque[tuple[str, str]] = deque(maxlen=500)
        self.last_status_at: str | None = None
        self.last_heartbeat_at: str | None = None
        self.last_services: dict[str, str] | None = None
        self.handoffs: int | None = None
        self.handoffs_reported_at: str | None = None
        self.handoffs_pending = 0

    def tell(self, at: str, text: str) -> list[Story]:
        match = _PREFIX.match(text)
        service, message = (match.group(1), match.group(2)) if match else (None, text)
        if service == "track_receiver" and message.startswith("track_update_received"):
            return self._track(at, fields(message))
        if service == "tracking_engine" and message.startswith("tracking_status"):
            return self._tracking(at, fields(message))
        if service == "infrastructure":
            return self._infrastructure(at, message)
        if service is None:
            return self._supervisor(at, message)
        if message.startswith("started pid="):
            return [Story(at, f"► Arranca {SERVICE_NAMES.get(service, service)}", "system")]
        if message.startswith("exited code="):
            return [Story(at, f"▲ Se ha detenido {SERVICE_NAMES.get(service, service)}; se reinicia solo", "alert")]
        if re.search(r"\bERROR\b|Traceback", message):
            return [Story(at, f"▲ Aviso de {SERVICE_NAMES.get(service, service)}", "alert")]
        return []

    def _track(self, at: str, data: dict[str, str]) -> list[Story]:
        camera, track, phase = data.get("camera"), data.get("track"), data.get("phase")
        if not camera or not track:
            return []
        where = self.names.name(camera).upper()
        key = (camera, track)
        if phase == "new":
            self.seen_since[key] = at
            return [Story(at, f"◆ {where} · nueva persona a la vista ({short_track(track)})", "event")]
        if phase == "end":
            since = self.seen_since.pop(key, None)
            seconds = seconds_between(since, at) if since else None
            stayed = f" tras {duration_text(seconds)}" if seconds is not None else ""
            return [Story(at, f"◇ {where} · {short_track(track)} sale de la imagen{stayed}", "event")]
        if phase == "snapshot":
            if key in self.photographed:
                return []
            self.photographed.append(key)
            what = ("fotos de cuerpo y cara de {} guardadas" if data.get("source") == "dahua"
                    else "foto de {} guardada").format(short_track(track))
            return [Story(at, f"▣ {where} · {what}", "event")]
        return []

    def _tracking(self, at: str, data: dict[str, str]) -> list[Story]:
        stories: list[Story] = []
        handoffs = _int(data.get("handoffs"))
        if handoffs is not None:
            if self.handoffs is not None and handoffs > self.handoffs:
                self.handoffs_pending += handoffs - self.handoffs
            self.handoffs = handoffs
            if self.handoffs_pending and self._due(self.handoffs_reported_at, at, self.handoff_every):
                count = self.handoffs_pending
                text = ("↔ El sistema relaciona dos apariciones: posible paso entre salas" if count == 1
                        else f"↔ El sistema relaciona {count} pares de apariciones entre salas")
                stories.append(Story(at, text, "event"))
                self.handoffs_pending = 0
                self.handoffs_reported_at = at
        active = _int(data.get("active"))
        if active is not None and self._due(self.last_status_at, at, self.status_every):
            self.last_status_at = at
            people = "Nadie a la vista" if active == 0 else (
                "1 persona en seguimiento" if active == 1 else f"{active} personas en seguimiento")
            stories.append(Story(at, f"● {people} ahora mismo", "status"))
        return stories

    def _infrastructure(self, at: str, message: str) -> list[Story]:
        if message.startswith("ERROR"):
            return [Story(at, "▲ Problema al arrancar una pieza del sistema", "alert")]
        lines = {
            "docker action=start_desktop": "► Encendiendo los contenedores (Docker)",
            "docker engine=ready": "► Contenedores listos",
            "mqtt ready": "► Mensajería lista",
            "frigate_gpu_detector ready": "► Detector de IA en la tarjeta gráfica listo",
            "frigate action=restart reason=started_before_detector": "► Reiniciando Frigate para conectarlo a la IA",
            "frigate action=restart reason=detector_restarted": "► Reiniciando Frigate para reconectarlo a la IA",
            "frigate ready": "► Frigate en marcha",
            "frigate detector=zmq": "► Frigate busca personas con la tarjeta gráfica",
            "frigate action=compose_stop": "■ Parando Frigate",
        }
        for prefix, story in lines.items():
            if message.startswith(prefix):
                return [Story(at, story, "system")]
        return []

    def _supervisor(self, at: str, message: str) -> list[Story]:
        if message.startswith("ERROR"):
            return [Story(at, "▲ El sistema no ha podido arrancar; reintentando", "alert")]
        if message.startswith("stack_starting"):
            count = fields(message).get("services", "")
            return [Story(at, f"► Arrancando el sistema ({count} servicios)", "system")]
        if message.startswith("stack_stopping"):
            return [Story(at, "■ Parando el sistema", "system")]
        if message.startswith("power keep_awake=on"):
            return [Story(at, "► El PC no se suspenderá mientras el sistema funcione", "system")]
        if message.startswith("stack_status"):
            return self._services(at, fields(message))
        return []

    def _services(self, at: str, states: dict[str, str]) -> list[Story]:
        stories: list[Story] = []
        previous = self.last_services
        self.last_services = states
        healthy = {"running", "up"}
        if previous is not None:
            for name, state in states.items():
                was = previous.get(name)
                if was in healthy and state not in healthy:
                    stories.append(Story(at, f"▲ {SERVICE_NAMES.get(name, name).capitalize()} no responde", "alert"))
                elif was is not None and was not in healthy and state in healthy:
                    stories.append(Story(at, f"● {SERVICE_NAMES.get(name, name).capitalize()} vuelve a funcionar", "status"))
        if not stories and self._due(self.last_heartbeat_at, at, self.heartbeat_every):
            self.last_heartbeat_at = at
            working = sum(state in healthy for state in states.values())
            stories.append(Story(at, f"● Sistema en marcha: {working} de {len(states)} piezas funcionando", "status"))
        return stories

    @staticmethod
    def _due(last: str | None, now: str, every: float) -> bool:
        if last is None:
            return True
        seconds = seconds_between(last, now)
        return seconds is None or seconds >= every


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
