"""Plan and optionally apply bounded retention to collector output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time


RETENTION_MARKER = ".dahua-collector-output"


@dataclass(frozen=True)
class RetentionPolicy:
    max_age_days: float = 7.0


@dataclass(frozen=True)
class RetentionCandidate:
    path: Path
    size: int
    reason: str


def plan_retention(
    root: Path, policy: RetentionPolicy, now: float | None = None
) -> list[RetentionCandidate]:
    current_time = time.time() if now is None else now
    if policy.max_age_days <= 0:
        raise ValueError("max_age_days must be positive")
    if not root.exists():
        return []
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != RETENTION_MARKER
    ]
    selected: list[RetentionCandidate] = []

    for path in files:
        stat = path.stat()
        if current_time - stat.st_mtime > policy.max_age_days * 86400:
            selected.append(RetentionCandidate(path, stat.st_size, "age"))

    return sorted(selected, key=lambda item: str(item.path))


def apply_retention(candidates: list[RetentionCandidate]) -> int:
    removed_bytes = 0
    for candidate in candidates:
        candidate.path.unlink(missing_ok=True)
        removed_bytes += candidate.size
    return removed_bytes
