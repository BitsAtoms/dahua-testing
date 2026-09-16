#!/usr/bin/env python3
"""Local HTTP server for editing ``space_map.v1``."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import signal
import socket
import sys
import threading
from typing import Any
from urllib.parse import urlparse

SERVICE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_ROOT))

from space_mapper import MapError, SpaceMapStore, discover_camera_ids
from space_mapper.monitor import monitor_snapshot
from space_mapper.monitor import validation_snapshot
from space_mapper.validation import ValidationError, ValidationStore


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
        elif path == "/api/monitor":
            space_map = self.server.store.load()
            camera_spaces = {
                camera["camera_id"]: camera["space_id"]
                for camera in space_map["cameras"]
            }
            self._json(
                HTTPStatus.OK,
                self.server.with_media_urls(
                    monitor_snapshot(
                        self.server.tracking_database,
                        self.server.evidence_database,
                        camera_spaces,
                        recent_seconds=self.server.recent_seconds,
                    )
                ),
            )
        elif path == "/api/validation":
            self.server.validation_store.cleanup()
            self._json(
                HTTPStatus.OK,
                self.server.with_media_urls(
                    {
                        "active": self.server.validation_store.active(),
                        "recent": self.server.validation_store.recent(),
                    }
                ),
            )
        elif path.startswith("/api/media/"):
            media = self.server.resolve_media(path.rsplit("/", 1)[-1])
            if media is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Imagen no disponible"})
            else:
                self._file(media, "image/jpeg")
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            document = self._body()
            if path == "/api/map":
                self.server.store.save(document)
                self._json(HTTPStatus.OK, {"ok": True})
                return
            match = re.fullmatch(r"/api/validation/([^/]+)/annotations", path)
            if match:
                session = self.server.validation_store.annotate(
                    match.group(1), document
                )
                self._json(
                    HTTPStatus.OK, self.server.with_media_urls(session)
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
        except (
            MapError,
            ValidationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/validation/start":
                document = self._body()
                session = self.server.validation_store.start(
                    str(document.get("name", "")),
                    document.get("expected_route", []),
                    document.get("subjects", []),
                )
                self._json(HTTPStatus.CREATED, session)
                return
            match = re.fullmatch(r"/api/validation/([^/]+)/complete", path)
            if match:
                session = self.server.validation_store.get(match.group(1))
                if session is None or session["status"] != "active":
                    raise ValidationError("active validation session not found")
                current = datetime.now(timezone.utc)
                ended_us = round(current.timestamp() * 1_000_000)
                space_map = self.server.store.load()
                camera_spaces = {
                    camera["camera_id"]: camera["space_id"]
                    for camera in space_map["cameras"]
                }
                report = validation_snapshot(
                    self.server.tracking_database,
                    self.server.evidence_database,
                    camera_spaces,
                    int(session["started_us"]),
                    ended_us,
                )
                completed = self.server.validation_store.complete(
                    session["session_id"], report, current
                )
                self._json(
                    HTTPStatus.OK, self.server.with_media_urls(completed)
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
        except (
            MapError,
            ValidationError,
            FileNotFoundError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise MapError("Tamaño de documento inválido")
        document = json.loads(self.rfile.read(length))
        if not isinstance(document, dict):
            raise MapError("El cuerpo debe ser un objeto JSON")
        return document

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
        tracking_database: Path,
        evidence_database: Path,
        validation_database: Path,
        recent_seconds: float,
    ) -> None:
        self.store = store
        self.receiver_database = receiver_database
        self.tracking_database = tracking_database
        self.evidence_database = evidence_database
        self.validation_store = ValidationStore(validation_database)
        self.recent_seconds = recent_seconds
        self.media: dict[str, Path] = {}
        self.media_lock = threading.Lock()
        super().__init__(address, MapHandler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def with_media_urls(self, document: Any) -> Any:
        result = json.loads(json.dumps(document, ensure_ascii=False))
        repository_root = SERVICE_ROOT.parents[1].resolve()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                media = value.get("media")
                if isinstance(media, list):
                    for item in media:
                        if not isinstance(item, dict) or "path" not in item:
                            continue
                        path = Path(str(item.pop("path"))).resolve()
                        try:
                            path.relative_to(repository_root)
                        except ValueError:
                            continue
                        if not path.is_file() or path.suffix.lower() not in {
                            ".jpg",
                            ".jpeg",
                        }:
                            continue
                        token = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
                        with self.media_lock:
                            self.media[token] = path
                        item["url"] = f"/api/media/{token}"
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(result)
        return result

    def resolve_media(self, token: str) -> Path | None:
        with self.media_lock:
            path = self.media.get(token)
        return path if path is not None and path.is_file() else None


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
    parser.add_argument(
        "--tracking-database",
        type=Path,
        default=Path("runtime/tracking-engine/tracking.sqlite3"),
    )
    parser.add_argument(
        "--evidence-database",
        type=Path,
        default=Path("runtime/visual-reid/evidence.sqlite3"),
    )
    parser.add_argument(
        "--validation-database",
        type=Path,
        default=Path("runtime/space-mapper/validation.sqlite3"),
    )
    parser.add_argument("--recent-seconds", type=float, default=120)
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("space mapper only listens on localhost")
    if args.recent_seconds <= 0:
        parser.error("recent-seconds must be positive")

    server = MapServer(
        (args.host, args.port),
        SpaceMapStore(args.map_file),
        args.receiver_database,
        args.tracking_database,
        args.evidence_database,
        args.validation_database,
        args.recent_seconds,
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
