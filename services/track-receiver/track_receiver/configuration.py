"""Local environment configuration shared by receiver MQTT commands."""

from __future__ import annotations

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line_number, original in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"malformed .env line {line_number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def merged_config(env_file: Path) -> dict[str, str]:
    return {**read_env(env_file), **os.environ}


def mqtt_config(config: dict[str, str]) -> dict[str, str | int]:
    """Resolve common transport settings with the Frigate broker as fallback."""
    host = config.get("TRACK_MQTT_HOST") or config.get("FRIGATE_MQTT_HOST")
    if not host:
        raise ValueError("TRACK_MQTT_HOST or FRIGATE_MQTT_HOST is required")
    return {
        "host": host,
        "port": int(
            config.get("TRACK_MQTT_PORT")
            or config.get("FRIGATE_MQTT_PORT", "1883")
        ),
        "topic": config.get("TRACK_MQTT_TOPIC", "tracking/track-updates"),
        "username": config.get("TRACK_MQTT_USER", ""),
        "password": config.get("TRACK_MQTT_PASSWORD", ""),
    }
