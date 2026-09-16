from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_ROOT = REPOSITORY_ROOT / "services" / "local-supervisor"
sys.path.insert(0, str(SUPERVISOR_ROOT))

from local_supervisor.configuration import ServiceSpec  # noqa: E402
from local_supervisor.processes import ManagedService  # noqa: E402


CHILD = """
import signal
import time

stopped = False

def request_stop(*_args):
    global stopped
    stopped = True

signal.signal(signal.SIGINT, request_stop)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, request_stop)
print("fixture_ready", flush=True)
while not stopped:
    time.sleep(0.02)
print("fixture_stopped", flush=True)
"""


class ManagedServiceTests(unittest.TestCase):
    def test_child_receives_clean_shutdown_and_writes_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ManagedService(
                ServiceSpec("fixture", (sys.executable, "-u", "-c", CHILD)),
                root,
                root / "logs",
                threading.Lock(),
            )
            service.start()
            deadline = time.monotonic() + 5
            while (
                time.monotonic() < deadline
                and (
                    not service.log_path.exists()
                    or "fixture_ready"
                    not in service.log_path.read_text(encoding="utf-8")
                )
            ):
                time.sleep(0.02)

            service.stop(timeout=5)

            log = service.log_path.read_text(encoding="utf-8")
            self.assertIn("fixture_ready", log)
            self.assertIn("fixture_stopped", log)
            self.assertIsNone(service.process)


if __name__ == "__main__":
    unittest.main()

