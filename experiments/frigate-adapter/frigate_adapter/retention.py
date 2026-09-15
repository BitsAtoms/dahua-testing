"""Seven-day retention for Frigate adapter output."""

from __future__ import annotations

from pathlib import Path
import time


RETENTION_DAYS = 7


def remove_expired_files(root: Path, now: float | None = None) -> tuple[int, int]:
    """Remove regular output files older than seven days."""
    if not root.exists():
        return 0, 0
    current_time = time.time() if now is None else now
    cutoff = current_time - RETENTION_DAYS * 86400
    removed_files = 0
    removed_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        if stat.st_mtime >= cutoff:
            continue
        path.unlink()
        removed_files += 1
        removed_bytes += stat.st_size
    return removed_files, removed_bytes
