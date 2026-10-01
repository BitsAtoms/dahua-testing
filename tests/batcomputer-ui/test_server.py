from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.console import SupervisorConsole  # noqa: E402
from batcomputer_ui.health import HealthMonitor, SupervisorWatch  # noqa: E402
from batcomputer_ui.screens import SCREENS  # noqa: E402
from server import UiServer  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        log = Path(cls.directory.name) / "20261001T083524Z" / "supervisor.log"
        log.parent.mkdir(parents=True)
        log.write_text("2026-10-01T08:35:24+00:00 stack_starting services=7\n", encoding="utf-8")
        watch = SupervisorWatch()
        console = SupervisorConsole(Path(cls.directory.name), observers=[watch.observe])
        # Offline sources: the test must not reach the real Frigate or broker.
        health = HealthMonitor(watch, frigate=lambda endpoint: None, tcp=lambda host, port: False,
                               refresh=console.refresh)
        cls.server = UiServer(("127.0.0.1", 0), console, health)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def get(self, path: str) -> tuple[int, str, bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.headers["Content-Type"], response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers["Content-Type"], error.read()

    def test_every_screen_has_a_page(self) -> None:
        for position_id in SCREENS:
            status, content_type, body = self.get(f"/screen/{position_id}")
            self.assertEqual(status, 200, position_id)
            self.assertTrue(content_type.startswith("text/html"))
            self.assertIn(b"/web/theme.css", body)

    def test_unknown_screen_is_not_found(self) -> None:
        status, _, _ = self.get("/screen/top_center")

        self.assertEqual(status, 404)

    def test_static_files_cannot_leave_the_web_folder(self) -> None:
        status, _, _ = self.get("/web/../server.py")
        self.assertEqual(status, 404)
        status, content_type, _ = self.get("/web/theme.css")
        self.assertEqual((status, content_type), (200, "text/css; charset=utf-8"))

    def test_screens_document_marks_built_pages(self) -> None:
        _, _, body = self.get("/api/screens")
        screens = {item["position_id"]: item for item in json.loads(body)["screens"]}

        self.assertEqual(set(screens), set(SCREENS))
        self.assertTrue(screens["mini_center"]["built"])
        self.assertEqual((screens["mini_center"]["width"], screens["mini_center"]["height"]), (960, 540))

    def test_console_api_returns_the_supervisor_lines(self) -> None:
        _, _, body = self.get("/api/console?after=0")
        data = json.loads(body)

        self.assertEqual([line["text"] for line in data["lines"]], ["stack_starting services=7"])
        status, _, _ = self.get("/api/console?after=x")
        self.assertEqual(status, 400)

    def test_system_health_lists_the_parts_in_a_fixed_order(self) -> None:
        status, _, body = self.get("/api/system-health")
        health = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in health["items"]],
                         ["docker", "mqtt", "frigate", "detector", "services", "delay"])
        self.assertEqual(health["overall"], "critical")


if __name__ == "__main__":
    unittest.main()
