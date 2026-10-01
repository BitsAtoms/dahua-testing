from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.health import HealthMonitor, SupervisorWatch, p95, receiver_delays  # noqa: E402


T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp()
HEALTHY_SERVICES = "stack_status mqtt=up frigate_gpu_detector=running frigate=up track_receiver=running tracking_engine=running"


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(T0 + seconds, timezone.utc).isoformat()


def camera(fps: float = 5.0, process: float = 5.0, skipped: float = 0.0, detections: float = 2.0) -> dict:
    return {"camera_fps": fps, "process_fps": process, "skipped_fps": skipped,
            "detection_fps": detections, "detection_enabled": True}


class Fake:
    def __init__(self) -> None:
        self.now = T0
        self.mqtt = True
        self.stats: dict | None = {"detectors": {"gpu": {"inference_speed": 33.0}},
                                   "cameras": {"recepcion": camera(), "reuniones": camera()}}
        self.config: dict | None = {"detectors": {"gpu": {"type": "zmq"}}}
        self.delays: list[float] = [3.0, 5.0, 25.0]

    def frigate(self, endpoint: str) -> dict | None:
        return self.stats if endpoint == "stats" else self.config


class HealthMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = Fake()
        self.watch = SupervisorWatch()
        self.watch.observe(iso(0), HEALTHY_SERVICES)
        self.monitor = HealthMonitor(
            self.watch,
            frigate=self.fake.frigate,
            tcp=lambda host, port: self.fake.mqtt,
            delays=lambda since: self.fake.delays,
            clock=lambda: self.fake.now,
            cache_seconds=0,
        )

    def items(self, at: float = 0) -> dict[str, tuple[str, str]]:
        self.fake.now = T0 + at
        return {item["id"]: (item["state"], item["value"]) for item in self.monitor.snapshot()["items"]}

    def test_a_healthy_system_reads_in_plain_words(self) -> None:
        items = self.items()

        self.assertEqual(items, {
            "docker": ("ok", "en marcha"),
            "mqtt": ("ok", "conectada"),
            "frigate": ("ok", "2 cámaras analizándose"),
            "detector": ("ok", "33 ms por imagen"),
            "services": ("ok", "2 de 2 en marcha"),
            "delay": ("ok", "25 ms"),
        })
        self.assertEqual(self.monitor.snapshot()["overall"], "ok")

    def test_blind_gpu_detector_is_an_error_but_a_fast_cpu_detector_is_not(self) -> None:
        self.fake.stats["detectors"]["gpu"]["inference_speed"] = 0.5
        self.assertEqual(self.items()["detector"], ("error", "ciego: no ve a nadie"))

        self.fake.config = {"detectors": {"ov": {"type": "openvino"}}}
        cpu = HealthMonitor(self.watch, frigate=self.fake.frigate, tcp=lambda h, p: True,
                            clock=lambda: self.fake.now, cache_seconds=0)
        detector = next(item for item in cpu.snapshot()["items"] if item["id"] == "detector")
        self.assertEqual((detector["label"], detector["state"]), ("Detector de IA · CPU", "ok"))

    def test_stalled_cameras_wait_out_the_restart_minute(self) -> None:
        self.fake.stats["cameras"]["recepcion"] = camera(process=1.0, skipped=4.0)

        self.assertEqual(self.items(0)["detector"][0], "ok")
        self.fake.stats["detectors"]["gpu"]["inference_speed"] = 34.0
        self.assertEqual(self.items(60)["detector"][0], "ok")
        self.fake.stats["detectors"]["gpu"]["inference_speed"] = 35.0
        self.assertEqual(self.items(95)["detector"], ("error", "atascado en 1 cámara"))

    def test_frozen_inference_time_with_detections_is_an_error(self) -> None:
        self.assertEqual(self.items(0)["detector"][0], "ok")
        self.assertEqual(self.items(299)["detector"][0], "ok")
        self.assertEqual(self.items(301)["detector"], ("error", "congelado"))

    def test_absurd_inference_time_and_a_stopped_gpu_program_are_errors(self) -> None:
        self.fake.stats["detectors"]["gpu"]["inference_speed"] = 4200.0
        self.assertEqual(self.items()["detector"], ("error", "roto: 4.2 s por imagen"))

        self.watch.observe(iso(1), HEALTHY_SERVICES.replace("frigate_gpu_detector=running", "frigate_gpu_detector=exited(1)"))
        self.assertEqual(self.items(1)["detector"], ("error", "programa de la GPU detenido"))

    def test_docker_down_and_a_silent_supervisor_are_critical(self) -> None:
        self.fake.mqtt = False
        self.fake.stats = None
        items = self.items(31)

        self.assertEqual(items["docker"], ("critical", "parados"))
        self.assertEqual(items["mqtt"], ("critical", "sin conexión"))
        self.assertEqual(items["frigate"], ("error", "no responde"))
        self.assertEqual(items["detector"][0], "unknown")
        self.assertEqual(items["services"], ("critical", "el supervisor no responde"))
        self.assertEqual(self.monitor.snapshot()["overall"], "critical")

    def test_stopped_and_restarting_services(self) -> None:
        self.watch.observe(iso(5), HEALTHY_SERVICES.replace("tracking_engine=running", "tracking_engine=waiting"))
        self.assertEqual(self.items(5)["services"], ("error", "detenido: seguimiento"))

        self.watch.observe(iso(6), HEALTHY_SERVICES)
        for n in range(4):
            self.watch.observe(iso(7 + n), "[visual_reid] exited code=1; restart_in=1s")
        self.assertEqual(self.items(12)["services"], ("warning", "4 reinicios en 10 min"))
        self.assertEqual(self.items(700)["services"][0], "critical")  # and silent by then

    def test_transport_delay(self) -> None:
        self.fake.delays = []
        self.assertEqual(self.items()["delay"], ("unknown", "sin datos recientes"))
        self.fake.delays = [10.0] * 90 + [2500.0] * 10
        self.assertEqual(self.items()["delay"], ("warning", "2.5 s"))

    def test_snapshots_are_cached(self) -> None:
        cached = HealthMonitor(self.watch, frigate=self.fake.frigate, tcp=lambda h, p: True,
                               clock=lambda: self.fake.now, cache_seconds=2)
        first = cached.snapshot()
        self.fake.now = T0 + 1
        self.assertIs(cached.snapshot(), first)
        self.fake.now = T0 + 3
        self.assertIsNot(cached.snapshot(), first)


class ReceiverDelayTests(unittest.TestCase):
    def test_recent_updates_give_publication_to_reception_delays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receiver.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE track_updates (published_at TEXT, receiver_received_at TEXT, receiver_received_us INTEGER)")
            connection.executemany("INSERT INTO track_updates VALUES (?,?,?)", [
                (iso(0), iso(0.003), int((T0 + 0.003) * 1e6)),
                (iso(100), iso(100.040), int((T0 + 100.040) * 1e6)),
            ])
            connection.commit()
            connection.close()

            delays = receiver_delays(database, T0 + 50)

        self.assertEqual(len(delays), 1)
        self.assertAlmostEqual(delays[0], 40.0, places=0)

    def test_missing_database_has_no_delays(self) -> None:
        self.assertEqual(receiver_delays(Path("missing.sqlite3"), T0), [])

    def test_p95(self) -> None:
        self.assertEqual(p95([float(n) for n in range(1, 101)]), 95.0)


if __name__ == "__main__":
    unittest.main()
