#!/usr/bin/env python3
"""Local control plane and web dashboard for Dahua collector workers."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import re
import signal
import socket
import threading
import time
from typing import Any
from urllib.parse import urlparse

from dahua_collector.configuration import CameraConfig, load_camera_config
from dahua_collector.retention import (
    RETENTION_MARKER,
    RetentionPolicy,
    apply_retention,
    plan_retention,
)
from live import read_env
from supervisor import CameraWorker


CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
OBSERVATION_PREFIX = "collector_observation="
MAX_CONSOLE_ENTRIES = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(start: Any, end: Any) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        elapsed = (
            datetime.fromisoformat(end.replace("Z", "+00:00"))
            - datetime.fromisoformat(start.replace("Z", "+00:00"))
        ).total_seconds() * 1000
    except (TypeError, ValueError):
        return None
    return round(elapsed, 1)


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _upsert_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    pending = dict(values)
    updated: list[str] = []
    for original in lines:
        stripped = original.strip()
        if not stripped or stripped.startswith("#") or "=" not in original:
            updated.append(original)
            continue
        key = original.split("=", 1)[0].strip()
        if key in pending:
            updated.append(f"{key}={pending.pop(key)}")
        else:
            updated.append(original)
    if updated and pending:
        updated.append("")
    updated.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


class ControlPlane:
    def __init__(
        self,
        config_path: Path,
        env_path: Path,
        output_root: Path,
        retention_days: float,
        retention_interval: float,
    ) -> None:
        self.config_path = config_path
        self.env_path = env_path
        self.output_root = output_root
        self.retention_policy = RetentionPolicy(max_age_days=retention_days)
        self.retention_interval = retention_interval
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.workers: dict[str, CameraWorker] = {}
        self.cameras: dict[str, CameraConfig] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.subscribers: list[queue.Queue[str]] = []
        self.media: dict[str, Path] = {}
        self.logs: deque[dict[str, Any]] = deque(maxlen=MAX_CONSOLE_ENTRIES)
        self._log_sequence = 0
        self.retention = {"last_run": None, "removed_files": 0, "removed_bytes": 0}
        self.live_script = Path(__file__).with_name("live.py").resolve()
        self.static_root = Path(__file__).with_name("web").resolve()
        self._thread = threading.Thread(target=self._run, name="control-plane", daemon=True)

    def start(self) -> None:
        if self.config_path.exists():
            for camera in load_camera_config(self.config_path):
                self.cameras[camera.camera_id] = camera
                self._initial_state(camera)
        for camera in self.cameras.values():
            if camera.enabled:
                self._start_worker(camera)
        self._log("info", None, f"Servicio iniciado con {len(self.cameras)} cámara(s)")
        self._thread.start()

    def close(self) -> None:
        self.stop_event.set()
        with self.lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.stop()
        self._thread.join(timeout=3)

    def _initial_state(self, camera: CameraConfig) -> dict[str, Any]:
        return self.states.setdefault(
            camera.camera_id,
            {
                "status": "stopped",
                "detail": "Collector detenido",
                "updated_at": _utc_now(),
                "messages": 0,
                "observations": 0,
                "last_observation": None,
                "pipeline_latency_ms": None,
                "latency_ms": {
                    "event_age_at_dashboard": None,
                    "cgi_to_dashboard": None,
                    "netsdk_callback_to_python": None,
                    "netsdk_callback_to_dashboard": None,
                    "normalized_to_dashboard": None,
                    "correlation_wait": None,
                },
                "channels": {
                    "process": "stopped",
                    "netsdk": "stopped",
                    "cgi": "stopped",
                    "transport": "stopped",
                },
            },
        )

    def _start_worker(self, camera: CameraConfig) -> None:
        secrets = read_env(self.env_path)
        worker = CameraWorker(
            camera, secrets, self.live_script, self.output_root, self.messages
        )
        self.workers[camera.camera_id] = worker
        state = self._initial_state(camera)
        state.update(status="starting", detail="Iniciando collector", updated_at=_utc_now())
        state["channels"] = {
            "process": "starting",
            "netsdk": "connecting",
            "cgi": "connecting",
            "transport": "connecting",
        }
        worker.start()

    def start_camera(self, camera_id: str) -> None:
        with self.lock:
            camera = self._camera(camera_id)
            worker = self.workers.get(camera_id)
            if worker is not None:
                worker.stop()
            self._start_worker(camera)
        self._publish("status")

    def stop_camera(self, camera_id: str) -> None:
        with self.lock:
            self._camera(camera_id)
            worker = self.workers.pop(camera_id, None)
            if worker is not None:
                worker.stop()
            self._initial_state(self.cameras[camera_id]).update(
                status="stopped", detail="Collector detenido", updated_at=_utc_now()
            )
            self.states[camera_id]["channels"] = {
                "process": "stopped",
                "netsdk": "stopped",
                "cgi": "stopped",
                "transport": "stopped",
            }
            self._log("info", camera_id, "Collector detenido por el operador")
        self._publish("status")

    def _camera(self, camera_id: str) -> CameraConfig:
        try:
            return self.cameras[camera_id]
        except KeyError as error:
            raise ValueError(f"Cámara desconocida: {camera_id}") from error

    def save_camera(self, payload: dict[str, Any]) -> None:
        camera_id = str(payload.get("camera_id", "")).strip()
        host = str(payload.get("host", "")).strip()
        if not CAMERA_ID_RE.fullmatch(camera_id):
            raise ValueError("camera_id admite letras, números, guion y guion bajo")
        if not HOST_RE.fullmatch(host):
            raise ValueError("host no es válido")
        sdk_port = self._port(payload.get("sdk_port", 37777), "sdk_port")
        http_port = self._port(payload.get("http_port", 80), "http_port")
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        safe_id = re.sub(r"[^A-Za-z0-9]", "_", camera_id).upper()
        username_env = f"DAHUA_{safe_id}_USER"
        password_env = f"DAHUA_{safe_id}_PASSWORD"

        existing_secrets = read_env(self.env_path) if self.env_path.exists() else {}
        existing = self.cameras.get(camera_id)
        if not username and existing is not None:
            username = existing_secrets.get(existing.username_env, "")
        if not password and existing is not None:
            password = existing_secrets.get(existing.password_env, "")
        if not username or not password:
            raise ValueError("usuario y contraseña son obligatorios para una cámara nueva")
        if any(character in username + password for character in "\r\n"):
            raise ValueError("usuario y contraseña no pueden contener saltos de línea")

        camera = CameraConfig(
            camera_id=camera_id,
            host=host,
            sdk_port=sdk_port,
            http_port=http_port,
            username_env=username_env,
            password_env=password_env,
            enabled=bool(payload.get("enabled", True)),
        )
        _upsert_env(self.env_path, {username_env: username, password_env: password})
        with self.lock:
            old_worker = self.workers.pop(camera_id, None)
            if old_worker is not None:
                old_worker.stop()
            self.cameras[camera_id] = camera
            _write_json_atomic(
                self.config_path,
                {"cameras": [self._camera_document(item) for item in self.cameras.values()]},
            )
            self._initial_state(camera).update(
                status="stopped", detail="Configuración guardada", updated_at=_utc_now()
            )
            self.states[camera_id]["channels"] = {
                "process": "stopped",
                "netsdk": "stopped",
                "cgi": "stopped",
                "transport": "stopped",
            }
            self._log("info", camera_id, "Configuración guardada")
            if camera.enabled:
                self._start_worker(camera)
        self._publish("status")

    @staticmethod
    def _port(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} no es válido")
        try:
            port = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} no es válido") from error
        if not 1 <= port <= 65535:
            raise ValueError(f"{name} debe estar entre 1 y 65535")
        return port

    @staticmethod
    def _camera_document(camera: CameraConfig) -> dict[str, Any]:
        return {
            "camera_id": camera.camera_id,
            "host": camera.host,
            "sdk_port": camera.sdk_port,
            "http_port": camera.http_port,
            "username_env": camera.username_env,
            "password_env": camera.password_env,
            "enabled": camera.enabled,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            cameras = []
            for camera in self.cameras.values():
                item = self._camera_document(camera)
                item.pop("username_env")
                item.pop("password_env")
                item["credentials_configured"] = True
                item["runtime"] = dict(self._initial_state(camera))
                cameras.append(item)
            return {
                "service": "dahua-collector",
                "now": _utc_now(),
                "retention_days": self.retention_policy.max_age_days,
                "retention": dict(self.retention),
                "logs": list(self.logs),
                "cameras": cameras,
            }

    def clear_logs(self) -> None:
        with self.lock:
            self.logs.clear()
        self._publish("logs")

    def _log(self, level: str, camera_id: str | None, message: str) -> None:
        self._log_sequence += 1
        self.logs.append(
            {
                "id": self._log_sequence,
                "at": _utc_now(),
                "level": level,
                "camera_id": camera_id,
                "message": message[:500],
            }
        )

    def resolve_media(self, token: str) -> Path | None:
        with self.lock:
            path = self.media.get(token)
        if path is None:
            return None
        try:
            path.resolve().relative_to(self.output_root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def subscribe(self) -> queue.Queue[str]:
        subscriber: queue.Queue[str] = queue.Queue(maxsize=100)
        with self.lock:
            self.subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[str]) -> None:
        with self.lock:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

    def _publish(self, kind: str, **details: Any) -> None:
        message = json.dumps(
            {"type": kind, "at": _utc_now(), **details}, separators=(",", ":")
        )
        with self.lock:
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(message)
                except queue.Empty:
                    pass

    def _run(self) -> None:
        next_retention = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_retention:
                candidates = plan_retention(self.output_root, self.retention_policy)
                removed_bytes = apply_retention(candidates)
                self.retention = {
                    "last_run": _utc_now(),
                    "removed_files": len(candidates),
                    "removed_bytes": removed_bytes,
                }
                if candidates:
                    with self.lock:
                        self._log(
                            "info",
                            None,
                            f"Retención eliminó {len(candidates)} archivo(s)",
                        )
                next_retention = now + self.retention_interval
            with self.lock:
                workers = list(self.workers.values())
            for worker in workers:
                worker.poll_and_restart()
            try:
                camera_id, message = self.messages.get(timeout=0.2)
            except queue.Empty:
                continue
            self._handle_worker_message(camera_id, message)

    def _handle_worker_message(self, camera_id: str, message: str) -> None:
        notification: dict[str, Any] = {}
        with self.lock:
            camera = self.cameras.get(camera_id)
            if camera is None:
                return
            state = self._initial_state(camera)
            state["updated_at"] = _utc_now()
            if message == "worker_started":
                state.update(status="starting", detail="Proceso iniciado")
                state["channels"]["process"] = "running"
                self._log("info", camera_id, "Proceso collector iniciado")
            elif message.startswith("netsdk: SDK version="):
                self._log("info", camera_id, message.removeprefix("netsdk: "))
            elif "Login succeeded" in message:
                state.update(status="connecting", detail="Login NetSDK correcto")
                state["channels"]["netsdk"] = "connected"
                self._log("info", camera_id, "Login NetSDK correcto")
            elif message.startswith("netsdk: Subscribed"):
                state["channels"]["netsdk"] = "running"
                self._log("info", camera_id, "Suscripción NetSDK activa")
            elif message == "netsdk: Live track subscription active.":
                self._log("info", camera_id, "Suscripción de tracks solicitada")
            elif message == "netsdk: Live track feed receiving updates":
                self._log("event", camera_id, "Canal de tracks en vivo confirmado")
            elif message.startswith("netsdk: Live track subscription unavailable"):
                self._log("warning", camera_id, "Canal de tracks no soportado")
            elif message.startswith("netsdk: Live track subscription error="):
                self._log("warning", camera_id, message.removeprefix("netsdk: "))
            elif message.startswith("netsdk: Analyzer event"):
                self._log("info", camera_id, message.removeprefix("netsdk: "))
            elif message == "status: CGI connected":
                state["channels"]["cgi"] = "running"
                self._log("info", camera_id, "Canal CGI conectado")
            elif message.startswith("track_transport_connected"):
                state["channels"]["transport"] = "running"
                self._log("info", camera_id, "Salida MQTT conectada")
            elif message.startswith(
                (
                    "track_transport_connection_failed",
                    "track_transport_disconnected",
                    "track_transport_retry",
                )
            ):
                state["channels"]["transport"] = "warning"
                self._log("warning", camera_id, message)
            elif message.startswith("track_transport_stopped"):
                state["channels"]["transport"] = "stopped"
            elif "Camera disconnected" in message:
                state["channels"]["netsdk"] = "warning"
                self._log("warning", camera_id, "Cámara desconectada; NetSDK reintentará")
            elif "Camera reconnected" in message:
                state["channels"]["netsdk"] = "running"
                self._log("info", camera_id, "Cámara reconectada por NetSDK")
            elif message.startswith(("warning:", "error:")) or "worker_exited" in message:
                if "CGI disconnected" in message:
                    state["channels"]["cgi"] = "warning"
                if "worker_exited" in message:
                    state["channels"] = {
                        "process": "warning",
                        "netsdk": "stopped",
                        "cgi": "stopped",
                        "transport": "stopped",
                    }
                elif "CGI disconnected" not in message:
                    state["channels"]["process"] = "warning"
                self._log(
                    "error" if message.startswith("error:") else "warning",
                    camera_id,
                    message,
                )
            elif message.startswith(OBSERVATION_PREFIX):
                self._record_observation(state, message[len(OBSERVATION_PREFIX):])
                state["channels"]["process"] = "running"
                observation = state.get("last_observation")
                if observation and observation.get("phase") in {"observation", "new"}:
                    event_age = state["latency_ms"].get("event_age_at_dashboard")
                    age_text = (
                        f" · antigüedad {event_age:g} ms"
                        if event_age is not None
                        else ""
                    )
                    self._log(
                        "event",
                        camera_id,
                        f"Observación recibida · track "
                        f"{observation.get('local_track_id') or 'sin ID'}"
                        f"{age_text}",
                    )
                if observation:
                    timing = observation.get("timing", {})
                    notification = {
                        "camera_id": camera_id,
                        "observed_at": observation.get("observed_at"),
                        "normalized_at": timing.get("normalized_at"),
                        "dashboard_received_at": timing.get("dashboard_received_at"),
                    }
            self._derive_status(state)
        self._publish(
            "observation" if message.startswith(OBSERVATION_PREFIX) else "status",
            **notification,
        )

    @staticmethod
    def _derive_status(state: dict[str, Any]) -> None:
        channels = state["channels"]
        if channels["process"] == "stopped":
            state.update(status="stopped", detail="Collector detenido")
        elif "warning" in channels.values():
            failed = [name.upper() for name, value in channels.items() if value == "warning"]
            state.update(status="warning", detail=f"Revisar: {', '.join(failed)}")
        elif all(
            channels.get(name) == "running"
            for name in ("netsdk", "cgi", "transport")
        ):
            state.update(status="running", detail="Captura y metadatos activos")
        else:
            state.update(status="connecting", detail="Conectando canales")

    def _record_observation(self, state: dict[str, Any], payload: str) -> None:
        try:
            observation = json.loads(payload)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            state.update(status="warning", detail="Evento de control inválido")
            return
        dashboard_received_at = _utc_now()
        timing = observation.setdefault("timing", {})
        timing["dashboard_received_at"] = dashboard_received_at
        latency = {
            # This is event age, not transport latency: Dahua's RealUTC may
            # identify an early/best frame and HumanTrait can be published
            # only when the camera finalizes the local track.
            "event_age_at_dashboard": _elapsed_ms(
                observation.get("observed_at"), dashboard_received_at
            ),
            "cgi_to_dashboard": _elapsed_ms(
                timing.get("cgi_received_at"), dashboard_received_at
            ),
            "netsdk_callback_to_python": _elapsed_ms(
                timing.get("netsdk_callback_received_at"),
                timing.get("netsdk_python_received_at"),
            ),
            "netsdk_callback_to_dashboard": _elapsed_ms(
                timing.get("netsdk_callback_received_at"), dashboard_received_at
            ),
            "normalized_to_dashboard": _elapsed_ms(
                timing.get("normalized_at"), dashboard_received_at
            ),
            "correlation_wait": timing.get("correlation_wait_ms"),
        }
        media_for_ui = []
        for item in observation.get("media", []):
            path = Path(item.get("path", ""))
            token = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
            self.media[token] = path
            media_for_ui.append({"role": item.get("role"), "url": f"/api/media/{token}"})
        observation["media"] = media_for_ui
        state["messages"] += 1
        if observation.get("phase") in {"observation", "new"}:
            state["observations"] += 1
        state["last_observation"] = observation
        state["latency_ms"] = latency
        state["pipeline_latency_ms"] = latency["normalized_to_dashboard"]
        state.update(status="running", detail="Recibiendo observaciones")


class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def handle(self) -> None:
        """Treat local browser disconnects as normal connection lifecycle."""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._file(self.server.control.static_root / "index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(HTTPStatus.OK, self.server.control.snapshot())
        elif path == "/api/events":
            self._events()
        elif path.startswith("/api/media/"):
            media = self.server.control.resolve_media(path.rsplit("/", 1)[-1])
            if media is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Imagen no disponible"})
            else:
                self._file(media, "image/jpeg", cache="private, max-age=60")
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/cameras":
                self.server.control.save_camera(self._body())
                self._json(HTTPStatus.OK, self.server.control.snapshot())
                return
            if path == "/api/logs/clear":
                self.server.control.clear_logs()
                self._json(HTTPStatus.OK, {"ok": True})
                return
            match = re.fullmatch(r"/api/cameras/([A-Za-z0-9_-]+)/(?P<action>start|stop)", path)
            if match:
                camera_id = match.group(1)
                if match.group("action") == "start":
                    self.server.control.start_camera(camera_id)
                else:
                    self.server.control.stop_camera(camera_id)
                self._json(HTTPStatus.OK, self.server.control.snapshot())
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length inválido") from error
        if not 0 < length <= 65536:
            raise ValueError("Cuerpo vacío o demasiado grande")
        document = json.loads(self.rfile.read(length))
        if not isinstance(document, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON")
        return document

    def _events(self) -> None:
        subscriber = self.server.control.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while not self.server.control.stop_event.is_set():
                try:
                    message = subscriber.get(timeout=15)
                    wire = f"data: {message}\n\n".encode("utf-8")
                except queue.Empty:
                    wire = b": heartbeat\n\n"
                self.wfile.write(wire)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.server.control.unsubscribe(subscriber)

    def _file(self, path: Path, content_type: str, cache: str = "no-store") -> None:
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Archivo no encontrado"})
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
        data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], control: ControlPlane) -> None:
        self.control = control
        super().__init__(address, DashboardHandler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("experiments/dahua-netsdk/cameras.local.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-root", type=Path, default=Path("experiments/dahua-netsdk/output"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--retention-days", type=float, default=7)
    parser.add_argument("--retention-interval-seconds", type=float, default=3600)
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("la consola de configuración solo puede escuchar en localhost")

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / RETENTION_MARKER).touch(exist_ok=True)
    control = ControlPlane(
        args.config.resolve(),
        args.env_file.resolve(),
        output_root,
        args.retention_days,
        args.retention_interval_seconds,
    )
    try:
        server = DashboardServer((args.host, args.port), control)
    except OSError as error:
        print(
            f"dashboard_start_failed address={args.host}:{args.port} error={error}",
            flush=True,
        )
        return 1
    try:
        control.start()
        print(f"dashboard=http://{args.host}:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping dashboard...", flush=True)
    finally:
        server.server_close()
        control.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
