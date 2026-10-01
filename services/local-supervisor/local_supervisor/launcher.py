"""Logon launcher: keep the supervisor running and open the map window once.

The Windows logon task runs this instead of the supervisor directly, so a
failed start (for example Docker Desktop not ready in time) is retried
instead of leaving the computer without a system.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, Protocol, TextIO
import urllib.request


MAP_URL = "http://127.0.0.1:8091/?view=monitor"
MAP_HEALTH_URL = "http://127.0.0.1:8091/api/health"
CHROME_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
)


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...


def find_chrome() -> Path | None:
    return next((path for path in CHROME_CANDIDATES if path.is_file()), None)


def map_window_command(chrome: Path, profile_dir: Path, url: str = MAP_URL) -> list[str]:
    """A Chrome app window (no tabs or address bar) in full screen; F11 toggles it.

    A dedicated profile keeps it apart from the user's own Chrome, so the
    flags apply even when that Chrome is already open.
    """
    return [
        str(chrome),
        f"--user-data-dir={profile_dir}",
        f"--app={url}",
        "--start-fullscreen",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def open_map_window(command: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        # Chrome must not receive the console's Ctrl+C or close with it.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def map_ready(url: str = MAP_HEALTH_URL) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


class DailyLog:
    """Console plus `autostart-YYYYMMDD.log`, removed by the 7-day retention."""

    def __init__(self, root: Path, console: TextIO | None = None) -> None:
        self.root = root
        # pythonw (the Ctrl+Alt+B shortcut) has no console: sys.stdout is None.
        self.console = console if console is not None else sys.stdout

    def __call__(self, message: str) -> None:
        now = datetime.now(timezone.utc)
        if self.console is not None:
            self.console.write(f"[autostart] {message}\n")
            self.console.flush()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"autostart-{now:%Y%m%d}.log"
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"{now.isoformat()} {message}\n")


class Launcher:
    def __init__(
        self,
        start_supervisor: Callable[[], Process],
        log: Callable[[str], None],
        stop: threading.Event,
        open_map: Callable[[], None] | None,
        map_ready: Callable[[], bool] = map_ready,
        retry_seconds: float = 30.0,
        poll_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.start_supervisor = start_supervisor
        self.log = log
        self.stop = stop
        self.open_map = open_map
        self.map_ready = map_ready
        self.retry_seconds = retry_seconds
        self.poll_seconds = poll_seconds
        self.sleep = sleep

    def run(self) -> int:
        map_opened = self.open_map is None
        attempt = 0
        while True:
            attempt += 1
            process = self.start_supervisor()
            self.log(f"supervisor started pid={process.pid} attempt={attempt}")
            while (code := process.poll()) is None:
                if not map_opened and not self.stop.is_set() and self.map_ready():
                    assert self.open_map is not None
                    self.open_map()
                    map_opened = True
                    self.log("map_window opened")
                # Not stop.wait: after Ctrl+C keep polling while the supervisor stops.
                self.sleep(self.poll_seconds)
            if code == 0 or self.stop.is_set():
                # 0 is a requested stop (Ctrl+C); the supervisor already cleaned up.
                self.log(f"supervisor exited code={code}; autostart done")
                return code
            self.log(f"supervisor exited code={code}; retry_in={self.retry_seconds:g}s")
            if self.stop.wait(self.retry_seconds):
                self.log("stop requested; autostart done")
                return code
