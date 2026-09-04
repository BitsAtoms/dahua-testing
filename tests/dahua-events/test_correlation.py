from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import os
import tempfile
import unittest


COLLECTOR_ROOT = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "dahua-netsdk"
    / "collector"
)
sys.path.insert(0, str(COLLECTOR_ROOT))

from dahua_collector import CgiHumanTraitStreamParser, DahuaEventCorrelator  # noqa: E402
from dahua_collector.configuration import CameraConfig, load_camera_config  # noqa: E402
from dahua_collector.retention import RetentionPolicy, plan_retention  # noqa: E402
from dahua_collector.sinks import JsonlEventSink  # noqa: E402
from dashboard import ControlPlane  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def body(group_id: int = 333) -> dict:
    return {
        "action": "Start",
        "object_type": "Human",
        "group_id": group_id,
        "object_id": 1520,
        "relative_id": 1001520,
        "belong_id": 1520,
        "event_id": 11112,
        "event_uuid": "cgi-body-uuid",
        "attributes": {"bag": "Unknown"},
        "raw_field": "preserved",
    }


def face() -> dict:
    return {
        "action": "Start",
        "object_type": "HumanFace",
        "group_id": 334,
        "object_id": 1001520,
        "relative_id": 1520,
        "belong_id": 1520,
        "event_id": 11113,
    }


def netsdk(group_id: int = 333) -> dict:
    return {
        "group_id": group_id,
        "timestamp": "20260904T104427-000",
        "snapshots": [
            {"type": "body", "file": "body.jpg"},
            {"type": "face", "file": "face.jpg"},
            {"type": "panoramic", "file": "scene.jpg"},
        ],
        "buffer_size": 831384,
    }


class DahuaEventCorrelatorTests(unittest.TestCase):
    def test_complete_body_face_and_netsdk_correlation(self) -> None:
        correlator = DahuaEventCorrelator()

        self.assertEqual(correlator.ingest_netsdk("cam-1", netsdk()), [])
        initial = correlator.ingest_cgi("cam-1", body())
        emitted = correlator.ingest_cgi("cam-1", face())

        self.assertEqual(len(initial), 2)
        self.assertEqual(initial[0]["phase"], "observation")
        self.assertEqual(initial[0]["quality"]["status"], "partial")
        self.assertEqual(initial[1]["phase"], "update")
        self.assertEqual(initial[1]["media"][1]["role"], "face")
        self.assertEqual(len(emitted), 1)
        event = emitted[0]
        self.assertEqual(event["schema_version"], "observation.v1")
        self.assertEqual(event["observation_id"], "dahua:cam-1:cgi-body-uuid")
        self.assertEqual(event["phase"], "update")
        self.assertEqual(event["subject"]["local_track_id"], "1520")
        self.assertEqual(event["relationships"]["face_local_object_id"], "1001520")
        self.assertEqual(event["source_data"]["group_id"], 333)
        self.assertEqual(event["source_data"]["face_event_id"], 11113)
        self.assertEqual(event["quality"]["status"], "complete")
        self.assertEqual(event["raw"]["cgi_body"]["raw_field"], "preserved")

    def test_cameras_with_same_group_id_do_not_mix(self) -> None:
        correlator = DahuaEventCorrelator()

        correlator.ingest_netsdk("cam-1", netsdk())
        correlator.ingest_netsdk("cam-2", netsdk())
        initial = correlator.ingest_cgi("cam-1", body())
        emitted = correlator.ingest_cgi("cam-1", face())

        self.assertEqual(len(initial), 2)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["camera_id"], "cam-1")
        self.assertEqual(correlator.expire(), [])

    def test_stop_and_duplicate_events_are_ignored(self) -> None:
        correlator = DahuaEventCorrelator()
        stop = body()
        stop["action"] = "Stop"
        self.assertEqual(correlator.ingest_cgi("cam-1", stop), [])

        correlator.ingest_netsdk("cam-1", netsdk())
        self.assertEqual(len(correlator.ingest_cgi("cam-1", body())), 2)
        self.assertEqual(len(correlator.ingest_cgi("cam-1", face())), 1)
        self.assertEqual(correlator.ingest_netsdk("cam-1", netsdk()), [])

    def test_ttl_emits_partial_event_when_face_metadata_is_missing(self) -> None:
        clock = FakeClock()
        correlator = DahuaEventCorrelator(ttl_seconds=5, clock=clock)
        initial = correlator.ingest_cgi("cam-1", body())
        media_update = correlator.ingest_netsdk("cam-1", netsdk())

        clock.now = 5.0
        emitted = correlator.expire()

        self.assertEqual(len(initial), 1)
        self.assertEqual(len(media_update), 1)
        self.assertEqual(media_update[0]["quality"]["status"], "partial")
        self.assertIsNone(
            media_update[0]["relationships"]["face_local_object_id"]
        )
        self.assertEqual(media_update[0]["media"][1]["role"], "face")
        self.assertEqual(emitted, [])

    def test_body_without_expected_face_emits_immediately(self) -> None:
        event = body()
        event["relative_id"] = 0
        correlator = DahuaEventCorrelator()

        initial = correlator.ingest_cgi("cam-1", event)
        emitted = correlator.ingest_netsdk("cam-1", netsdk())

        self.assertEqual(len(initial), 1)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["quality"]["status"], "complete")
        self.assertIsNone(
            emitted[0]["relationships"]["face_local_object_id"]
        )

    def test_body_is_visible_before_media_and_face_arrive(self) -> None:
        correlator = DahuaEventCorrelator()

        emitted = correlator.ingest_cgi("cam-1", body())

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["phase"], "observation")
        self.assertEqual(emitted[0]["media"], [])
        self.assertEqual(emitted[0]["subject"]["local_track_id"], "1520")


class CgiHumanTraitStreamParserTests(unittest.TestCase):
    def test_parses_multiline_body_event(self) -> None:
        parser = CgiHumanTraitStreamParser()
        lines = [
            "--myboundary",
            "Code=HumanTrait;action=Start;index=0;data={",
            '  "EventID": 11112,',
            '  "GroupID": 333,',
            '  "WithSnap": true,',
            '  "Objects": [{',
            '    "ObjectID": 1520,',
            '    "RelativeID": 1001520,',
            '    "BelongID": 1520,',
            '    "ObjectType": "Human"',
            "  }]",
            "}",
        ]

        records = []
        for line in lines:
            records.extend(parser.feed_line(line))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["group_id"], 333)
        self.assertEqual(records[0]["object_id"], 1520)
        self.assertEqual(records[0]["relative_id"], 1001520)


class CameraConfigurationTests(unittest.TestCase):
    def test_example_configuration_loads(self) -> None:
        path = COLLECTOR_ROOT.parent / "cameras.example.json"
        cameras = load_camera_config(path)

        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0].camera_id, "dahua_213")
        self.assertEqual(cameras[0].sdk_port, 37777)

    def test_worker_environment_resolves_secret_references(self) -> None:
        camera = CameraConfig(
            camera_id="cam-1",
            host="192.0.2.1",
            sdk_port=37777,
            http_port=80,
            username_env="CAM_USER",
            password_env="CAM_PASSWORD",
        )

        environment = camera.worker_environment(
            {"CAM_USER": "operator", "CAM_PASSWORD": "secret"}
        )

        self.assertEqual(environment["DAHUA_USER"], "operator")
        self.assertEqual(environment["DAHUA_PASSWORD"], "secret")


class OutputTests(unittest.TestCase):
    def test_jsonl_sink_appends_one_event_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            with JsonlEventSink(path) as sink:
                sink.publish({"event_uid": "one"})
                sink.publish({"event_uid": "two"})

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn('"event_uid":"one"', lines[0])

    def test_retention_planning_is_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "old.jpg"
            snapshot.write_bytes(b"jpeg")
            os.utime(snapshot, (0, 0))
            policy = RetentionPolicy(max_age_days=7)

            candidates = plan_retention(root, policy, now=10 * 86400)

            self.assertEqual([item.path for item in candidates], [snapshot])
            self.assertEqual(candidates[0].reason, "age")
            self.assertTrue(snapshot.exists())


class DashboardTests(unittest.TestCase):
    def test_camera_credentials_are_saved_but_never_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "cameras.local.json"
            secrets = root / ".env"
            output = root / "output"
            output.mkdir()
            control = ControlPlane(config, secrets, output, 7, 3600)

            control.save_camera(
                {
                    "camera_id": "front_door",
                    "host": "192.0.2.10",
                    "sdk_port": 37777,
                    "http_port": 80,
                    "username": "operator",
                    "password": "secret",
                    "enabled": False,
                }
            )

            snapshot = control.snapshot()
            serialized = json.dumps(snapshot)
            self.assertNotIn("operator", serialized)
            self.assertNotIn("secret", serialized)
            self.assertTrue(snapshot["cameras"][0]["credentials_configured"])
            self.assertIn("DAHUA_FRONT_DOOR_USER=operator", secrets.read_text())
            self.assertIn("DAHUA_FRONT_DOOR_PASSWORD=secret", secrets.read_text())

    def test_observation_projection_updates_metrics_and_media_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            image = output / "body.jpg"
            image.write_bytes(b"jpeg")
            control = ControlPlane(root / "cameras.json", root / ".env", output, 7, 3600)
            state = {
                "status": "starting",
                "detail": "",
                "messages": 0,
                "observations": 0,
                "last_observation": None,
                "pipeline_latency_ms": None,
            }
            payload = json.dumps(
                {
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "phase": "observation",
                    "media": [{"role": "body", "path": str(image)}],
                }
            )

            control._record_observation(state, payload)

            self.assertEqual(state["messages"], 1)
            self.assertEqual(state["observations"], 1)
            self.assertTrue(state["last_observation"]["media"][0]["url"].startswith("/api/media/"))
            self.assertGreaterEqual(state["pipeline_latency_ms"], 0)

    def test_common_contract_is_valid_json(self) -> None:
        schema = json.loads(
            (COLLECTOR_ROOT.parents[2] / "contracts" / "observation-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "observation.v1")


if __name__ == "__main__":
    unittest.main()
