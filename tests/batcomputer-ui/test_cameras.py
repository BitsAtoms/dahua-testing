from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.cameras import CameraHealth, last_seen, seen_text  # noqa: E402
from batcomputer_ui.narrator import CameraNames  # noqa: E402


NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc).timestamp()


def frigate_camera(fps: float, detect: bool = True) -> dict:
    return {"camera_fps": fps, "process_fps": fps, "skipped_fps": 0, "detection_fps": 1, "detection_enabled": detect}


def dahua_camera(camera_id: str, enabled: bool = True, netsdk: str = "connected", cgi: str = "running") -> dict:
    # The collector also reports host and ports; they must never reach the screen.
    return {"camera_id": camera_id, "host": "camera-host.invalid", "enabled": enabled, "runtime": {
        "status": "connecting", "channels": {"process": "running", "netsdk": netsdk, "cgi": cgi}}}


class CameraHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        map_file = Path(self.directory.name) / "space-map.json"
        map_file.write_text(json.dumps({"schema_version": "space_map.v2", "floors": [{
            "rooms": [{"id": "s1", "name": "Recepción"}, {"id": "s2", "name": "Showroom"}],
            "cameras": [{"camera_id": "cam_recepcion", "room_id": "s1"},
                        {"camera_id": "dahua_212", "room_id": "s2"}],
        }]}), encoding="utf-8")
        self.stats: dict | None = {"cameras": {
            "cam_recepcion": frigate_camera(5.1),
            "puerta": frigate_camera(10.0, detect=False),
        }}
        self.status: dict | None = {"cameras": [dahua_camera("dahua_212"), dahua_camera("dahua_213", enabled=False)]}
        self.seen = {"cam_recepcion": NOW - 125, "dahua_212": NOW - 3}
        self.health = CameraHealth(
            CameraNames(map_file),
            frigate=lambda endpoint: self.stats,
            dahua=lambda: self.status,
            seen=lambda: self.seen,
            clock=lambda: NOW,
            cache_seconds=0,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def cameras(self) -> dict[str, tuple[str, str, str, bool]]:
        return {c["id"]: (c["name"], c["state"], c["value"], c["active"]) for c in self.health.snapshot()["cameras"]}

    def test_the_editor_can_place_every_known_camera(self) -> None:
        self.seen["old_camera"] = NOW - 9000
        cameras = {item["camera_id"]: (item["source"], item["counts"], item["note"])
                   for item in self.health.placeable()}

        self.assertEqual(cameras, {
            "cam_recepcion": ("frigate", True, ""),
            "puerta": ("frigate", False, "solo vista"),
            "dahua_212": ("dahua", True, ""),
            "dahua_213": ("dahua", False, "desactivada"),
            "old_camera": ("", True, "sin fuente ahora"),
        })

    def test_connected_cameras_with_their_last_activity(self) -> None:
        snapshot = self.health.snapshot()

        self.assertEqual(self.cameras(), {
            "cam_recepcion": ("Recepción", "ok", "5,1 img/s · hace 2 min", False),
            "puerta": ("Puerta", "ok", "solo vista · 10,0 img/s", False),
            "dahua_212": ("Showroom", "ok", "NetSDK + CGI · viendo personas", True),
            "dahua_213": ("Dahua 213", "unknown", "desactivada", False),
        })
        self.assertEqual((snapshot["connected"], snapshot["total"]), (3, 3))
        self.assertEqual(snapshot["overall"], "ok")  # the disabled camera does not count
        self.assertNotIn("camera-host.invalid", json.dumps(snapshot))

    def test_lost_image_and_lost_channels_are_alarms(self) -> None:
        self.stats["cameras"]["cam_recepcion"] = frigate_camera(0)
        self.status = {"cameras": [dahua_camera("dahua_212", netsdk="disconnected")]}

        cameras = self.cameras()

        self.assertEqual(cameras["cam_recepcion"][1:3], ("critical", "sin imagen"))
        self.assertEqual(cameras["dahua_212"][1:3], ("critical", "sin conexión"))
        self.assertEqual(self.health.snapshot()["overall"], "critical")

        self.status = {"cameras": [dahua_camera("dahua_212", cgi="stopped")]}
        self.assertEqual(self.cameras()["dahua_212"][1:3], ("error", "sin eventos (CGI)"))

    def test_cameras_stay_listed_while_their_source_is_down(self) -> None:
        self.health.snapshot()
        self.stats = None
        self.status = None

        cameras = self.cameras()

        self.assertEqual(cameras["cam_recepcion"][1:3], ("unknown", "Frigate no responde"))
        self.assertEqual(cameras["dahua_212"][1:3], ("unknown", "colector sin respuesta"))

    def test_seen_text(self) -> None:
        self.assertEqual(seen_text(None, NOW), ("", False))
        self.assertEqual(seen_text(NOW - 2, NOW), ("viendo personas", True))
        self.assertEqual(seen_text(NOW - 42, NOW), ("hace 42 s", False))
        self.assertEqual(seen_text(NOW - 7300, NOW), ("hace 2 h", False))


class LastSeenTests(unittest.TestCase):
    def test_newest_update_per_camera(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receiver.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE track_updates (camera_id TEXT, receiver_received_us INTEGER)")
            connection.executemany("INSERT INTO track_updates VALUES (?,?)",
                                   [("a", 1_000_000), ("a", 5_000_000), ("b", 2_000_000)])
            connection.commit()
            connection.close()

            self.assertEqual(last_seen(database), {"a": 5.0, "b": 2.0})

    def test_missing_database(self) -> None:
        self.assertEqual(last_seen(Path("missing.sqlite3")), {})


if __name__ == "__main__":
    unittest.main()
