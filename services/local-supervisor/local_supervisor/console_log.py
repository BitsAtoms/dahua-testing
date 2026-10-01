"""Copy the supervisor's console output to a timestamped log file."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import threading
from typing import Callable, TextIO


class TeeLog(io.TextIOBase):
    """Console stream that also appends every complete line, timestamped, to a file.

    An unattended start has nobody watching the console, so restarts, exits
    and infrastructure steps must also reach a file.
    """

    def __init__(
        self,
        console: TextIO,
        path: Path,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.console = console
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8", newline="\n")
        self.now = now
        self.pending = ""
        self.lock = threading.Lock()

    def write(self, text: str) -> int:
        with self.lock:
            self.console.write(text)
            self.pending += text
            *lines, self.pending = self.pending.split("\n")
            for line in lines:
                line = line.rstrip("\r")
                if line:
                    self.file.write(f"{self.now().isoformat()} {line}\n")
            return len(text)

    def flush(self) -> None:
        with self.lock:
            self.console.flush()
            self.file.flush()

    def close(self) -> None:
        if self.closed:
            return
        with self.lock:
            if self.pending.strip():
                self.file.write(f"{self.now().isoformat()} {self.pending.rstrip()}\n")
            self.pending = ""
        super().close()  # flushes both streams
        self.file.close()
