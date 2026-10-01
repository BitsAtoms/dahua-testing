from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_ROOT = REPOSITORY_ROOT / "services" / "local-supervisor"
sys.path.insert(0, str(SUPERVISOR_ROOT))

from local_supervisor.console_log import TeeLog  # noqa: E402
from local_supervisor.launcher import DailyLog, Launcher, map_window_command  # noqa: E402


class FakeProcess:
    """Exits with `code` after `polls` polls."""

    def __init__(self, pid: int, code: int, polls: int = 2) -> None:
        self.pid = pid
        self.code = code
        self.polls = polls

    def poll(self) -> int | None:
        if self.polls > 0:
            self.polls -= 1
            return None
        return self.code


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log: list[str] = []
        self.opened = 0
        self.stop = threading.Event()

    def open_map(self) -> None:
        self.opened += 1

    def launcher(self, processes: list[FakeProcess], ready: bool = True, map_window: bool = True) -> Launcher:
        queue = iter(processes)
        return Launcher(
            lambda: next(queue),
            self.log.append,
            self.stop,
            self.open_map if map_window else None,
            map_ready=lambda: ready,
            retry_seconds=0,
            sleep=lambda _seconds: None,
        )

    def test_failed_start_is_retried_and_clean_exit_ends_the_launcher(self) -> None:
        code = self.launcher([FakeProcess(1, 3), FakeProcess(2, 0)]).run()

        self.assertEqual(code, 0)
        self.assertIn("supervisor exited code=3; retry_in=0s", self.log)
        self.assertIn("supervisor started pid=2 attempt=2", self.log)
        self.assertEqual(self.log[-1], "supervisor exited code=0; autostart done")

    def test_map_window_opens_once_across_supervisor_restarts(self) -> None:
        self.launcher([FakeProcess(1, 3), FakeProcess(2, 0)]).run()

        self.assertEqual(self.opened, 1)

    def test_map_window_waits_until_the_map_answers(self) -> None:
        self.launcher([FakeProcess(1, 0)], ready=False).run()

        self.assertEqual(self.opened, 0)

    def test_map_window_can_be_disabled(self) -> None:
        self.launcher([FakeProcess(1, 0)], map_window=False).run()

        self.assertEqual(self.opened, 0)

    def test_stop_request_waits_for_supervisor_and_does_not_retry(self) -> None:
        self.stop.set()

        code = self.launcher([FakeProcess(1, 3, polls=5)]).run()

        self.assertEqual(code, 3)
        self.assertEqual(self.opened, 0)
        self.assertEqual(sum(line.startswith("supervisor started") for line in self.log), 1)

    def test_map_window_is_a_full_screen_app_window_with_its_own_profile(self) -> None:
        command = map_window_command(Path("chrome.exe"), Path("profile"))

        self.assertIn("--app=http://127.0.0.1:8091/?view=monitor", command)
        self.assertIn("--start-fullscreen", command)
        self.assertIn(f"--user-data-dir={Path('profile')}", command)
        self.assertNotIn("--kiosk", command)  # --kiosk cannot be left with F11

    def test_daily_log_without_console_still_writes_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = DailyLog(Path(directory), console=None)
            log.console = None  # pythonw: no console at all

            log("map_window opened by shortcut")

            files = list(Path(directory).glob("autostart-*.log"))
            self.assertEqual(len(files), 1)
            self.assertIn("map_window opened by shortcut", files[0].read_text(encoding="utf-8"))


class TeeLogTests(unittest.TestCase):
    def test_complete_lines_are_timestamped_in_the_file_and_copied_to_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            console = io.StringIO()
            path = Path(directory) / "session" / "supervisor.log"
            moment = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
            tee = TeeLog(console, path, now=lambda: moment)

            tee.write("stack_starting ")
            tee.write("services=7\n[infrastructure] mqtt ready\r\n\npartial")
            tee.close()

            self.assertEqual(
                console.getvalue(),
                "stack_starting services=7\n[infrastructure] mqtt ready\r\n\npartial",
            )
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                [
                    "2026-10-01T09:00:00+00:00 stack_starting services=7",
                    "2026-10-01T09:00:00+00:00 [infrastructure] mqtt ready",
                    "2026-10-01T09:00:00+00:00 partial",
                ],
            )


class InstallScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (REPOSITORY_ROOT / "deploy/windows/install-autostart.ps1").read_text(
            encoding="utf-8"
        )

    def test_script_is_ascii_for_windows_powershell(self) -> None:
        # Windows PowerShell 5.1 reads BOM-less files with the ANSI code page.
        self.assertTrue(self.script.isascii())

    def test_task_runs_without_time_limit_at_normal_priority_for_the_user(self) -> None:
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", self.script)
        self.assertIn("$Settings.Priority = 4", self.script)
        self.assertIn("-AtLogOn -User $User", self.script)
        self.assertIn("-LogonType Interactive -RunLevel Limited", self.script)

    def test_shortcut_hotkey_opens_the_map_without_a_console(self) -> None:
        self.assertIn('$Link.Hotkey = "CTRL+ALT+B"', self.script)
        self.assertIn("$Link.TargetPath = $PythonW", self.script)
        self.assertRegex(self.script, re.escape('--open-map"'))


if __name__ == "__main__":
    unittest.main()
