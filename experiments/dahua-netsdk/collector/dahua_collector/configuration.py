"""Load and validate non-secret Dahua camera configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CameraConfig:
    camera_id: str
    host: str
    sdk_port: int
    http_port: int
    username_env: str
    password_env: str
    enabled: bool = True

    def worker_environment(self, secrets: dict[str, str]) -> dict[str, str]:
        try:
            username = secrets[self.username_env]
            password = secrets[self.password_env]
        except KeyError as error:
            raise ValueError(
                f"{self.camera_id}: missing secret variable {error.args[0]}"
            ) from error
        if not username or not password:
            raise ValueError(f"{self.camera_id}: username and password cannot be empty")
        environment = {
            "DAHUA_HOST": self.host,
            "DAHUA_PORT": str(self.sdk_port),
            "DAHUA_HTTP_PORT": str(self.http_port),
            "DAHUA_USER": username,
            "DAHUA_PASSWORD": password,
        }
        # Diagnostic switches are global and optional. They live in the
        # ignored environment file but are not credentials.
        if "DAHUA_LIVE_TRACK_PROBE" in secrets:
            environment["DAHUA_LIVE_TRACK_PROBE"] = secrets[
                "DAHUA_LIVE_TRACK_PROBE"
            ]
        # Pass only the known transport settings to each isolated worker.
        for key in (
            "TRACK_MQTT_HOST",
            "TRACK_MQTT_PORT",
            "TRACK_MQTT_TOPIC",
            "TRACK_MQTT_USER",
            "TRACK_MQTT_PASSWORD",
            "TRACK_OUTBOX_ROOT",
            "FRIGATE_MQTT_HOST",
            "FRIGATE_MQTT_PORT",
            "FRIGATE_MQTT_USER",
            "FRIGATE_MQTT_PASSWORD",
        ):
            if key in secrets:
                environment[key] = secrets[key]
        return environment


def _valid_port(value: Any, field: str, camera_id: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError(f"{camera_id}: {field} must be an integer from 1 to 65535")
    return value


def load_camera_config(path: Path) -> list[CameraConfig]:
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("cameras")
    if not isinstance(entries, list) or not entries:
        raise ValueError("configuration must contain a non-empty cameras array")

    cameras = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each camera configuration must be an object")
        camera_id = entry.get("camera_id")
        host = entry.get("host")
        if not isinstance(camera_id, str) or not camera_id.strip():
            raise ValueError("camera_id must be a non-empty string")
        if camera_id in seen_ids:
            raise ValueError(f"duplicate camera_id: {camera_id}")
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"{camera_id}: host must be a non-empty string")

        username_env = entry.get("username_env")
        password_env = entry.get("password_env")
        if not isinstance(username_env, str) or not username_env:
            raise ValueError(f"{camera_id}: username_env is required")
        if not isinstance(password_env, str) or not password_env:
            raise ValueError(f"{camera_id}: password_env is required")

        cameras.append(
            CameraConfig(
                camera_id=camera_id,
                host=host,
                sdk_port=_valid_port(entry.get("sdk_port"), "sdk_port", camera_id),
                http_port=_valid_port(
                    entry.get("http_port", 80), "http_port", camera_id
                ),
                username_env=username_env,
                password_env=password_env,
                enabled=bool(entry.get("enabled", True)),
            )
        )
        seen_ids.add(camera_id)
    return cameras
