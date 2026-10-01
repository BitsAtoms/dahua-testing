from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.console import SupervisorConsole  # noqa: E402
from batcomputer_ui.narrator import CameraNames, Narrator, duration_text, short_track  # noqa: E402


def at(seconds: int) -> str:
    return f"2026-10-01T08:{seconds // 60:02d}:{seconds % 60:02d}+00:00"


def receiver(camera: str, phase: str, track: str, source: str = "dahua") -> str:
    return (f"[track_receiver] track_update_received source={source} camera={camera} "
            f"phase={phase} track={track} inserted=true store=1.2ms callback_to_store=1.5ms")


class NarratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        map_file = Path(self.directory.name) / "space-map.json"
        map_file.write_text(json.dumps({
            "spaces": [{"id": "s1", "name": "Recepción"}, {"id": "s2", "name": "Reuniones"}],
            "cameras": [
                {"camera_id": "cam_a", "label": "cam_a", "space_id": "s1"},
                {"camera_id": "cam_b", "label": "Puerta norte", "space_id": "s2"},
            ],
        }), encoding="utf-8")
        self.narrator = Narrator(CameraNames(map_file))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def tell(self, seconds: int, text: str) -> list[str]:
        return [story.text for story in self.narrator.tell(at(seconds), text)]

    def test_a_person_seen_and_lost_by_a_camera_reads_in_plain_words(self) -> None:
        self.assertEqual(self.tell(0, receiver("cam_a", "new", "4509")),
                         ["◆ RECEPCIÓN · nueva persona a la vista (#4509)"])
        self.assertEqual(self.tell(5, receiver("cam_a", "update", "4509")), [])
        self.assertEqual(self.tell(42, receiver("cam_a", "end", "4509")),
                         ["◇ RECEPCIÓN · #4509 sale de la imagen tras 42 s"])

    def test_photos_are_told_once_per_person(self) -> None:
        first = self.tell(10, receiver("cam_b", "snapshot", "77"))
        again = self.tell(11, receiver("cam_b", "snapshot", "77"))
        frigate = self.tell(12, receiver("cam_a", "snapshot", "1790843775.15853-vsjrw5", source="frigate"))

        self.assertEqual(first, ["▣ PUERTA NORTE · fotos de cuerpo y cara de #77 guardadas"])
        self.assertEqual(again, [])
        self.assertEqual(frigate, ["▣ RECEPCIÓN · foto de #vsjrw5 guardada"])

    def test_routine_internals_are_dropped(self) -> None:
        for line in (
            "[tracking_engine] tracking_batch scanned=1 applied=1 duplicates=0 cursor=101644",
            "[visual_reid] visual_reid_status candidates=929 processed=0 pending=0 stored=1327 cleanup=0",
            "[detector_consensus] detector_consensus_shadow camera=cam_a proposed=excluded",
            "[frigate_adapter] track_update camera=cam_a phase=update track=x event_age=743.4ms",
            "[frigate_gpu_detector] 2026-10-01 17:43:32,075 - __main__ - INFO - Loading model",
        ):
            self.assertEqual(self.tell(0, line), [], line)

    def test_tracking_summary_is_rate_limited(self) -> None:
        status = "[tracking_engine] tracking_status active={} ended=695 handoffs=534 pending=0"

        self.assertEqual(self.tell(0, status.format(2)), ["● 2 personas en seguimiento ahora mismo"])
        self.assertEqual(self.tell(10, status.format(1)), [])
        self.assertEqual(self.tell(60, status.format(0)), ["● Nadie a la vista ahora mismo"])

    def test_new_handoff_candidates_are_told_together(self) -> None:
        status = "[tracking_engine] tracking_status active=1 ended=1 handoffs={} pending=0"
        self.tell(0, status.format(10))

        self.assertIn("↔ El sistema relaciona 2 pares de apariciones entre salas",
                      self.tell(20, status.format(12)))
        self.assertEqual(self.tell(25, status.format(13)), [])
        self.assertIn("↔ El sistema relaciona dos apariciones: posible paso entre salas",
                      self.tell(40, status.format(13)))

    def test_service_changes_are_alerts_and_steady_state_is_a_rare_heartbeat(self) -> None:
        healthy = "stack_status mqtt=up frigate=up track_receiver=running"
        broken = "stack_status mqtt=up frigate=down track_receiver=running"

        self.assertEqual(self.tell(0, healthy), ["● Sistema en marcha: 3 de 3 piezas funcionando"])
        self.assertEqual(self.tell(10, healthy), [])
        self.assertEqual(self.tell(20, broken), ["▲ Frigate no responde"])
        self.assertEqual(self.tell(30, healthy), ["● Frigate vuelve a funcionar"])

    def test_start_up_steps_and_failures_are_translated(self) -> None:
        self.assertEqual(self.tell(0, "[infrastructure] frigate_gpu_detector ready after=0.5s"),
                         ["► Detector de IA en la tarjeta gráfica listo"])
        self.assertEqual(self.tell(0, "[infrastructure] frigate action=restart reason=started_before_detector"),
                         ["► Reiniciando Frigate para conectarlo a la IA"])
        self.assertEqual(self.tell(0, "[tracking_engine] started pid=15334"),
                         ["► Arranca el motor de seguimiento"])
        stories = self.narrator.tell(at(0), "[frigate_adapter] exited code=1; restart_in=1s")
        self.assertEqual([(s.text, s.kind) for s in stories],
                         [("▲ Se ha detenido la conexión con Frigate; se reinicia solo", "alert")])

    def test_helpers(self) -> None:
        self.assertEqual(short_track("1790843775.15853-vsjrw5"), "#vsjrw5")
        self.assertEqual(short_track("4509"), "#4509")
        self.assertEqual(duration_text(158), "2 min 38 s")
        self.assertEqual(duration_text(42.4), "42 s")

    def test_unknown_camera_falls_back_to_a_readable_id(self) -> None:
        self.assertEqual(self.tell(0, receiver("dahua_212", "new", "1")),
                         ["◆ DAHUA 212 · nueva persona a la vista (#1)"])


class ConsoleNarrationTests(unittest.TestCase):
    def test_console_keeps_visitor_lines_and_the_newest_raw_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "20261001T080000Z" / "supervisor.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                f"{at(0)} {receiver('cam_a', 'new', '9')}\n"
                f"{at(1)} [tracking_engine] tracking_batch scanned=1 applied=1\n",
                encoding="utf-8",
            )
            console = SupervisorConsole(root, narrator=Narrator(CameraNames(root / "missing.json")))

            data = console.read()

        self.assertEqual([(line["text"], line["kind"]) for line in data["lines"]],
                         [("◆ CAM A · nueva persona a la vista (#9)", "event")])
        self.assertEqual(data["last_line_at"], at(1))


if __name__ == "__main__":
    unittest.main()
