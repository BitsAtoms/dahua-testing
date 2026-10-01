#!/usr/bin/env python3
"""Entry point of the Windows logon task: supervisor plus the map window.

    autostart.py                  run the supervisor, retry failed starts,
                                  open the map window when the map answers
    autostart.py --no-map-window  same without the map window
    autostart.py --open-map       only open the map window (Ctrl+Alt+B shortcut)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import subprocess
import sys
import threading

from local_supervisor.launcher import (
    DailyLog,
    Launcher,
    find_chrome,
    map_window_command,
    open_map_window,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = REPOSITORY_ROOT / "services/local-supervisor/run.py"
LOG_ROOT = REPOSITORY_ROOT / "runtime/local-supervisor/logs"
MAP_PROFILE = REPOSITORY_ROOT / "runtime/map-window/chrome-profile"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-map-window", action="store_true")
    parser.add_argument("--open-map", action="store_true")
    args = parser.parse_args()
    log = DailyLog(LOG_ROOT)

    chrome = find_chrome()
    if chrome is None and (args.open_map or not args.no_map_window):
        log("ERROR chrome_not_found map_window=disabled")
    command = map_window_command(chrome, MAP_PROFILE) if chrome is not None else None
    if args.open_map:
        if command is None:
            return 2
        open_map_window(command)
        log("map_window opened by shortcut")
        return 0

    stop = threading.Event()

    def request_stop(_signal_number: int, _frame: object) -> None:
        # The supervisor in the same console receives the same Ctrl+C and
        # stops cleanly; wait for it instead of exiting first.
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    def start_supervisor() -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-u", str(SUPERVISOR)], cwd=REPOSITORY_ROOT)

    open_map = None
    if command is not None and not args.no_map_window:
        open_map = lambda: open_map_window(command)  # noqa: E731
    log(f"autostart starting map_window={'on' if open_map else 'off'}")
    return Launcher(start_supervisor, log, stop, open_map).run()


if __name__ == "__main__":
    raise SystemExit(main())
