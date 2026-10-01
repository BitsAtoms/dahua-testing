"""Follow the supervisor's console log for the mini_center screen."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .narrator import Narrator


SESSION_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")
# Defensive masking: the screen is visible to visitors.
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_SECRET_VALUES = re.compile(
    r"\b(password|passwd|pwd|token|secret|authorization)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)


def mask_secrets(text: str) -> str:
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    return _SECRET_VALUES.sub(r"\1\2***", text)


@dataclass(frozen=True)
class ConsoleLine:
    seq: int
    at: str
    text: str
    kind: str = "raw"


class SupervisorConsole:
    """Tails `<log_root>/<newest session>/supervisor.log`.

    Each line of that file is `<UTC ISO timestamp> <message>` (TeeLog). A new
    session folder, created at every supervisor start, is followed from its
    beginning. Lines keep increasing sequence numbers across sessions.
    """

    def __init__(self, log_root: Path, keep: int = 300, tail_bytes: int = 256 * 1024,
                 narrator: "Narrator | None" = None) -> None:
        self.log_root = log_root
        # With a narrator the console keeps visitor lines instead of raw ones.
        self.narrator = narrator
        # A long session's log holds hundreds of thousands of lines: start near its end.
        self.tail_bytes = tail_bytes
        self.lines: deque[ConsoleLine] = deque(maxlen=keep)
        self.session: str | None = None
        self.offset = 0
        self.pending = b""
        self.next_seq = 1
        # Time of the newest raw log line: the supervisor is alive while it moves.
        self.last_line_at: str | None = None
        self.lock = threading.Lock()

    def read(self, after: int = 0) -> dict:
        with self.lock:
            self._poll()
            lines = [line for line in self.lines if line.seq > after]
            return {
                "session": self.session,
                "last_seq": self.next_seq - 1,
                "last_line_at": self.last_line_at,
                "lines": [line.__dict__ for line in lines],
            }

    def _newest_session(self) -> Path | None:
        if not self.log_root.is_dir():
            return None
        sessions = sorted(
            path for path in self.log_root.iterdir()
            if path.is_dir() and SESSION_PATTERN.match(path.name)
        )
        return sessions[-1] if sessions else None

    def _poll(self) -> None:
        newest = self._newest_session()
        if newest is None:
            return
        path = newest / "supervisor.log"
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        skip_partial = False
        if newest.name != self.session:
            self.session = newest.name
            self.offset = max(0, size - self.tail_bytes)
            skip_partial = self.offset > 0
            self.pending = b""
        if size < self.offset:
            self.offset = 0
            self.pending = b""
        if size == self.offset:
            return
        with path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read(size - self.offset)
        self.offset = size
        data = self.pending + chunk
        *complete, self.pending = data.split(b"\n")
        if skip_partial and complete:
            complete = complete[1:]  # started in the middle of a line
        for raw in complete:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if not line.strip():
                continue
            at, _, text = line.partition(" ")
            self.last_line_at = at
            text = mask_secrets(text)
            if self.narrator is None:
                self._append(at, text, "raw")
                continue
            for story in self.narrator.tell(at, text):
                self._append(story.at, story.text, story.kind)

    def _append(self, at: str, text: str, kind: str) -> None:
        self.lines.append(ConsoleLine(self.next_seq, at, text, kind))
        self.next_seq += 1
