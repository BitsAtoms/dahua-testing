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

from batcomputer_ui.console import SupervisorConsole
from batcomputer_ui.screens import SCREENS, screens_document


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
        elif path == "/api/screens":
            self._json(HTTPStatus.OK, screens_document(built_screens()))
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

    def __init__(self, address: tuple[str, int], console: SupervisorConsole) -> None:
        super().__init__(address, UiHandler)
        self.console = console


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument(
        "--supervisor-logs",
        type=Path,
        default=REPOSITORY_ROOT / "runtime/local-supervisor/logs",
    )
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("batcomputer ui only listens on localhost")

    server = UiServer((args.host, args.port), SupervisorConsole(args.supervisor_logs))
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
