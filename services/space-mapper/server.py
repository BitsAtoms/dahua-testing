#!/usr/bin/env python3
"""Local HTTP server for editing ``space_map.v1``."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import socket
import sys
from typing import Any
from urllib.parse import urlparse

SERVICE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_ROOT))

from space_mapper import MapError, SpaceMapStore, discover_camera_ids


MAX_BODY_BYTES = 1_000_000


class MapHandler(BaseHTTPRequestHandler):
    server: "MapServer"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._file(SERVICE_ROOT / "web" / "index.html", "text/html; charset=utf-8")
        elif path == "/api/map":
            self._json(HTTPStatus.OK, self.server.store.load())
        elif path == "/api/cameras":
            self._json(
                HTTPStatus.OK,
                {"camera_ids": discover_camera_ids(self.server.receiver_database)},
            )
        elif path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/map":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise MapError("Tamaño de documento inválido")
            document = json.loads(self.rfile.read(length))
            self.server.store.save(document)
            self._json(HTTPStatus.OK, {"ok": True})
        except (MapError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            return

    def _file(self, path: Path, content_type: str) -> None:
        wire = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(wire)))
        self.end_headers()
        self.wfile.write(wire)

    def _json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
        wire = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(wire)))
        self.end_headers()
        self.wfile.write(wire)


class MapServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        store: SpaceMapStore,
        receiver_database: Path,
    ) -> None:
        self.store = store
        self.receiver_database = receiver_database
        super().__init__(address, MapHandler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument(
        "--map-file",
        type=Path,
        default=Path("runtime/space-mapper/space-map.json"),
    )
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("space mapper only listens on localhost")

    server = MapServer(
        (args.host, args.port), SpaceMapStore(args.map_file), args.receiver_database
    )
    try:
        print(f"space_mapper=http://{args.host}:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping space mapper...", flush=True)
    finally:
        server.server_close()
        print("space_mapper_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
