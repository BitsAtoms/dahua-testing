"""Local configuration helpers that never export or print secret values."""

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
    """Prefer exported variables over values read from a local dotenv file."""
    return {**read_env(env_file), **os.environ}
