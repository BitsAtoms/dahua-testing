"""Health of the cameras for mini_right: is each one connected, and when did
it last see someone.

- Frigate cameras: `camera_fps` from Frigate's `/api/stats` (0 means no image).
- Dahua cameras: the collector's `/api/status`, by its NetSDK and CGI channels
  (its `status` field can say "connecting" while both channels are up).
- Last activity: the newest track update per camera in the receiver database.

No address or credential of a camera leaves this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable
import urllib.request

from .health import STATE_ORDER, frigate_json
from .narrator import CameraNames


ACTIVE_SECONDS = 10


@dataclass(frozen=True)
class CameraItem:
    id: str
    name: str
    source: str
    state: str
    value: str
    active: bool = False
    enabled: bool = True
    # False for a view-only camera: it shows images but counts nobody.
    detects: bool = True


def dahua_status(url: str = "http://127.0.0.1:8090/api/status") -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            document = json.load(response)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def last_seen(database: Path) -> dict[str, float]:
    """Seconds since the epoch of each camera's newest track update."""
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                "SELECT camera_id, MAX(receiver_received_us) FROM track_updates GROUP BY camera_id"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {camera: received / 1_000_000 for camera, received in rows if received}


def seen_text(seen: float | None, now: float) -> tuple[str, bool]:
    if seen is None:
        return "", False
    age = max(0.0, now - seen)
    if age < ACTIVE_SECONDS:
        return "viendo personas", True
    if age < 60:
        return f"hace {int(age)} s", False
    if age < 3600:
        return f"hace {int(age // 60)} min", False
    return f"hace {int(age // 3600)} h", False


def decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


class CameraHealth:
    def __init__(
        self,
        names: CameraNames,
        frigate: Callable[[str], dict | None] = frigate_json,
        dahua: Callable[[], dict | None] = dahua_status,
        seen: Callable[[], dict[str, float]] = lambda: {},
        clock: Callable[[], float] = time.time,
        cache_seconds: float = 2.0,
    ) -> None:
        self.names = names
        self.frigate = frigate
        self.dahua = dahua
        self.seen = seen
        self.clock = clock
        self.cache_seconds = cache_seconds
        # Cameras stay listed, as unknown, while their source does not answer.
        self.frigate_cameras: list[str] = []
        self.dahua_cameras: list[str] = []
        self.cached: dict | None = None
        self.cached_at = 0.0
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if self.cached is None or now - self.cached_at >= self.cache_seconds:
                self.cached = self._evaluate(now)
                self.cached_at = now
            return self.cached

    def _evaluate(self, now: float) -> dict[str, Any]:
        seen = self.seen()
        items = self._frigate_items(now, seen) + self._dahua_items(now, seen)
        # Cameras switched off on purpose go last.
        items.sort(key=lambda item: (not item.enabled, item.name))
        watched = [item for item in items if item.enabled]
        connected = sum(item.state == "ok" for item in watched)
        # A camera switched off on purpose does not count against the others.
        worst = max((item.state for item in watched), key=STATE_ORDER.__getitem__, default="unknown")
        return {
            "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "overall": worst,
            "connected": connected,
            "total": len(watched),
            "cameras": [asdict(item) for item in items],
        }

    def placeable(self) -> list[dict[str, Any]]:
        """Cameras the space editor can place: every camera of a source,
        plus any camera that sent tracks and no source lists any more."""
        listed = self.snapshot()["cameras"]
        known = {item["id"] for item in listed}
        cameras = [
            {"camera_id": item["id"], "source": item["source"],
             "counts": item["enabled"] and item.get("detects", True),
             "note": "desactivada" if not item["enabled"]
             else "solo vista" if not item.get("detects", True) else ""}
            for item in listed
        ]
        cameras += [
            {"camera_id": camera_id, "source": "", "counts": True, "note": "sin fuente ahora"}
            for camera_id in sorted(set(self.seen()) - known)
        ]
        return cameras

    def _frigate_items(self, now: float, seen: dict[str, float]) -> list[CameraItem]:
        stats = self.frigate("stats")
        if stats is not None:
            self.frigate_cameras = sorted(stats.get("cameras", {}))
        items = []
        for camera_id in self.frigate_cameras:
            name = self.names.name(camera_id)
            camera = (stats or {}).get("cameras", {}).get(camera_id)
            if camera is None:
                items.append(CameraItem(camera_id, name, "frigate", "unknown", "Frigate no responde"))
                continue
            fps = camera.get("camera_fps") or 0
            if fps <= 0:
                items.append(CameraItem(camera_id, name, "frigate", "critical", "sin imagen"))
            elif not camera.get("detection_enabled"):
                items.append(CameraItem(camera_id, name, "frigate", "ok", f"solo vista · {decimal(fps)} img/s",
                                        detects=False))
            else:
                text, active = seen_text(seen.get(camera_id), now)
                value = f"{decimal(fps)} img/s" + (f" · {text}" if text else "")
                items.append(CameraItem(camera_id, name, "frigate", "ok", value, active))
        return items

    def _dahua_items(self, now: float, seen: dict[str, float]) -> list[CameraItem]:
        status = self.dahua()
        cameras = {}
        if status is not None:
            cameras = {camera.get("camera_id"): camera for camera in status.get("cameras", []) if camera.get("camera_id")}
            self.dahua_cameras = sorted(cameras)
        items = []
        for camera_id in self.dahua_cameras:
            name = self.names.name(camera_id)
            camera = cameras.get(camera_id)
            if camera is None:
                items.append(CameraItem(camera_id, name, "dahua", "unknown", "colector sin respuesta"))
                continue
            if not camera.get("enabled"):
                items.append(CameraItem(camera_id, name, "dahua", "unknown", "desactivada", enabled=False))
                continue
            channels = (camera.get("runtime") or {}).get("channels") or {}
            if channels.get("netsdk") != "connected":
                items.append(CameraItem(camera_id, name, "dahua", "critical", "sin conexión"))
            elif channels.get("cgi") != "running":
                items.append(CameraItem(camera_id, name, "dahua", "error", "sin eventos (CGI)"))
            else:
                text, active = seen_text(seen.get(camera_id), now)
                items.append(CameraItem(camera_id, name, "dahua", "ok",
                                        "NetSDK + CGI" + (f" · {text}" if text else ""), active))
        return items
