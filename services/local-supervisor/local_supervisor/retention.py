"""Bounded retention for supervisor-owned text logs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


RETENTION_DAYS = 7


def cleanup_logs(root: Path, now: datetime | None = None) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    resolved_root = root.resolve()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    removed_files = 0
    removed_bytes = 0
    for path in root.rglob("*.log"):
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            continue
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if modified >= cutoff:
            continue
        path.unlink()
        removed_files += 1
        removed_bytes += stat.st_size
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed_files, removed_bytes

