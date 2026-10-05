#!/usr/bin/env python3
"""Local HTTP server for the Batcomputer screens (one page per screen)."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVICE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SERVICE_ROOT.parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from functools import partial

from batcomputer_ui.cameras import CameraHealth, last_seen
from batcomputer_ui.console import SupervisorConsole
from batcomputer_ui.health import HealthMonitor, SupervisorWatch, receiver_delays
from batcomputer_ui.live_map import LiveMap, read_tracking
from batcomputer_ui.narrator import CameraNames, Narrator
from batcomputer_ui.screens import SCREENS, screens_document
from batcomputer_ui.spaces import SpaceMapConflict, SpaceMapError, SpaceMapStore


WEB_ROOT = SERVICE_ROOT / "web"
SCREEN_PAGES = WEB_ROOT / "screens"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_PLAN_BYTES = 1_000_000


def built_screens() -> set[str]:
    return {path.stem for path in SCREEN_PAGES.glob("*.html") if path.stem in SCREENS}


class UiHandler(BaseHTTPRequestHandler):
    server: "UiServer"

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/preview")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/preview":
            self._file(WEB_ROOT / "preview.html")
        elif path == "/editor":
            self._file(WEB_ROOT / "editor" / "editor.html")
        elif path.startswith("/screen/"):
            position_id = path.removeprefix("/screen/")
            if position_id not in SCREENS:
                self._json(HTTPStatus.NOT_FOUND, {"error": "unknown screen"})
                return
            page = SCREEN_PAGES / f"{position_id}.html"
            self._file(page if page.is_file() else WEB_ROOT / "pending.html")
        elif path.startswith("/web/"):
            target = self._inside(WEB_ROOT, path.removeprefix("/web/"))
            if target is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._file(target)
        elif path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
        elif path == "/api/system-health":
            self._json(HTTPStatus.OK, self.server.health.snapshot())
        elif path == "/api/camera-health":
            self._json(HTTPStatus.OK, self.server.cameras.snapshot())
        elif path == "/api/screens":
            self._json(HTTPStatus.OK, screens_document(built_screens()))
        elif path == "/api/live-map":
            if self.server.live_map is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._json(HTTPStatus.OK, self.server.live_map.snapshot())
        elif path == "/api/space-map/cameras":
            self._json(HTTPStatus.OK, {"cameras": self.server.cameras.placeable()})
        elif path == "/api/space-map":
            store = self.server.spaces
            if store is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                plan, revision = store.load()
            except ValueError as error:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": f"el plano guardado no es válido: {error}"})
                return
            self._json(HTTPStatus.OK, {"plan": plan, "revision": revision})
        elif path == "/api/console":
            after = parse_qs(url.query).get("after", ["0"])[0]
            try:
                after_seq = max(0, int(after))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "after must be an integer"})
                return
            self._json(HTTPStatus.OK, self.server.console.read(after_seq))
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_PUT(self) -> None:
        store = self.server.spaces
        if urlparse(self.path).path != "/api/space-map" or store is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        # Only pages served from this machine may save: a page from another
        # site open in the same browser must not overwrite the plan.
        if not self._local_request():
            self._json(HTTPStatus.FORBIDDEN, {"error": "solo se guarda desde este PC"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "se espera JSON"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_PLAN_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > 0 else HTTPStatus.BAD_REQUEST,
                       {"error": "el plano llega vacío o es demasiado grande"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        if not isinstance(body, dict) or set(body) != {"plan", "revision"}:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "se espera {plan, revision}"})
            return
        try:
            revision = store.save(body["plan"], body["revision"])
        except SpaceMapError as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except SpaceMapConflict as error:
            self._json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self._json(HTTPStatus.OK, {"revision": revision})

    def _local_request(self) -> bool:
        host = urlparse("//" + self.headers.get("Host", "")).hostname
        origin = self.headers.get("Origin")
        return host in LOCAL_HOSTS and (origin is None or urlparse(origin).hostname in LOCAL_HOSTS)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            return

    @staticmethod
    def _inside(root: Path, relative: str) -> Path | None:
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return None
        return target if target.is_file() and target.suffix in CONTENT_TYPES else None

    def _file(self, path: Path) -> None:
        wire = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES[path.suffix])
        self.send_header("Content-Length", str(len(wire)))
        # Pages change while the owner reviews them: never serve a stale copy.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(wire)

    def _json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
        wire = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(wire)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(wire)


class UiServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], console: SupervisorConsole,
                 health: HealthMonitor | None = None, cameras: CameraHealth | None = None,
                 spaces: SpaceMapStore | None = None, live_map: LiveMap | None = None) -> None:
        super().__init__(address, UiHandler)
        self.console = console
        self.health = health if health is not None else HealthMonitor(SupervisorWatch())
        self.cameras = cameras if cameras is not None else CameraHealth(CameraNames(Path("missing.json")))
        self.spaces = spaces
        self.live_map = live_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument(
        "--supervisor-logs",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/local-supervisor/logs",
    )
    parser.add_argument(
        "--map-file",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/space-mapper/space-map.json",
        help="space_map.v1 of the current editor, read for room names",
    )
    parser.add_argument(
        "--space-map",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/spaces/space-map.json",
        help="space_map.v2 building plan written by the new editor",
    )
    parser.add_argument(
        "--tracking-database",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/tracking-engine/tracking.sqlite3",
    )
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/track-receiver/receiver.sqlite3",
    )
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("batcomputer ui only listens on localhost")

    watch = SupervisorWatch()
    names = CameraNames(args.map_file)
    console = SupervisorConsole(
        args.supervisor_logs,
        narrator=Narrator(names),
        observers=[watch.observe],
    )
    health = HealthMonitor(
        watch,
        delays=partial(receiver_delays, args.receiver_database),
        refresh=console.refresh,
    )
    cameras = CameraHealth(names, seen=partial(last_seen, args.receiver_database))
    spaces = SpaceMapStore(args.space_map)
    live_map = LiveMap(spaces, partial(read_tracking, args.tracking_database))
    server = UiServer((args.host, args.port), console, health, cameras, spaces, live_map)
    try:
        print(f"batcomputer_ui=http://{args.host}:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping batcomputer ui...", flush=True)
    finally:
        server.server_close()
        print("batcomputer_ui_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
