from __future__ import annotations

from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
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

from dahua_collector import (  # noqa: E402
    CgiHumanTraitStreamParser,
    DahuaEventCorrelator,
    observation_to_track_update,
)
from dahua_collector.configuration import CameraConfig, load_camera_config  # noqa: E402
from dahua_collector.retention import RetentionPolicy, plan_retention  # noqa: E402
from dahua_collector.sinks import JsonlEventSink  # noqa: E402
from dashboard import ControlPlane  # noqa: E402
from live import write_normalized  # noqa: E402


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
        "timestamp": 1788511446,
        "_received_at": "2026-09-04T08:44:06.100000+00:00",
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
        "callback_received_at": "2026-09-04T08:44:06.120Z",
        "_python_received_at": "2026-09-04T08:44:06.180000+00:00",
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
        self.assertEqual(event["observed_at"], "2026-09-04T08:44:06+00:00")
        self.assertEqual(
            event["timing"]["netsdk_callback_received_at"],
            "2026-09-04T08:44:06.120Z",
        )
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

    def test_dahua_geometry_is_normalized_for_tracking(self) -> None:
        event = body()
        event["bounding_box"] = [819, 1638, 4096, 6554]
        event["center"] = [2458, 4096]

        observation = DahuaEventCorrelator().ingest_cgi("cam-1", event)[0]

        self.assertEqual(
            observation["geometry"]["coordinate_space"], "normalized_0_1"
        )
        self.assertAlmostEqual(
            observation["geometry"]["box"]["x_min"], 0.1, places=3
        )
        self.assertAlmostEqual(
            observation["geometry"]["box"]["y_max"], 0.8, places=3
        )
        self.assertAlmostEqual(
            observation["geometry"]["center"]["x"], 0.3, places=3
        )


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
        self.assertNotIn("DAHUA_LIVE_TRACK_PROBE", environment)

        diagnostic_environment = camera.worker_environment(
            {
                "CAM_USER": "operator",
                "CAM_PASSWORD": "secret",
                "DAHUA_LIVE_TRACK_PROBE": "1",
            }
        )
        self.assertEqual(diagnostic_environment["DAHUA_LIVE_TRACK_PROBE"], "1")

    def test_worker_environment_passes_only_known_transport_settings(self) -> None:
        camera = CameraConfig(
            camera_id="cam-1",
            host="192.0.2.1",
            sdk_port=37777,
            http_port=80,
            username_env="CAM_USER",
            password_env="CAM_PASSWORD",
        )

        environment = camera.worker_environment(
            {
                "CAM_USER": "operator",
                "CAM_PASSWORD": "secret",
                "TRACK_MQTT_HOST": "127.0.0.1",
                "TRACK_MQTT_TOPIC": "tracking/track-updates",
                "UNRELATED_SECRET": "must-not-leak",
            }
        )

        self.assertEqual(environment["TRACK_MQTT_HOST"], "127.0.0.1")
        self.assertEqual(
            environment["TRACK_MQTT_TOPIC"], "tracking/track-updates"
        )
        self.assertNotIn("UNRELATED_SECRET", environment)


class OutputTests(unittest.TestCase):
    def test_write_normalized_persists_and_enqueues_same_update(self) -> None:
        observation = DahuaEventCorrelator().ingest_cgi("cam-1", body())[0]
        observations = RecordingSink()
        track_updates = RecordingSink()
        publisher = RecordingSink()

        with redirect_stdout(io.StringIO()):
            write_normalized(
                observations,
                track_updates,
                publisher,
                [observation],
            )

        self.assertEqual(len(observations.events), 1)
        self.assertEqual(len(track_updates.events), 1)
        self.assertEqual(track_updates.events, publisher.events)
        self.assertEqual(
            track_updates.events[0]["message_id"],
            f"track-update:{observation['message_id']}",
        )

    def test_dahua_observation_projects_to_common_track_update(self) -> None:
        correlator = DahuaEventCorrelator()
        observation = correlator.ingest_cgi("cam-1", body())[0]
        observation["timing"]["collector_published_at"] = (
            "2026-09-04T08:44:06.200000+00:00"
        )

        update = observation_to_track_update(observation)

        self.assertEqual(update["schema_version"], "track_update.v1")
        self.assertEqual(update["track_id"], observation["observation_id"])
        self.assertEqual(update["phase"], "snapshot")
        self.assertEqual(update["sequence"], 1)
        self.assertEqual(update["subject"]["local_track_id"], "1520")
        self.assertEqual(update["quality"]["source_lifecycle"], "finalized_only")
        self.assertEqual(
            update["source_ref"]["event_id"], observation["observation_id"]
        )
        self.assertEqual(update["attributes"], {})
        self.assertNotIn("raw", update)

        schema = json.loads(
            (
                COLLECTOR_ROOT.parents[2]
                / "contracts"
                / "track-update-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(update), set(schema["required"]))
        self.assertIn(update["phase"], schema["properties"]["phase"]["enum"])

    def test_track_update_revision_is_preserved(self) -> None:
        correlator = DahuaEventCorrelator()
        correlator.ingest_netsdk("cam-1", netsdk())
        observations = correlator.ingest_cgi("cam-1", body())
        observation = observations[1]

        update = observation_to_track_update(observation)

        self.assertEqual(observation["message_id"].split(":")[-1], "r2")
        self.assertEqual(update["sequence"], 2)
        self.assertEqual(len(update["media"]), 3)

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


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> bool:
        self.events.append(event)
        return True


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
            now = datetime.now(timezone.utc).isoformat()
            payload = json.dumps(
                {
                    "ingested_at": now,
                    "observed_at": now,
                    "phase": "observation",
                    "media": [{"role": "body", "path": str(image)}],
                    "timing": {
                        "camera_observed_at": now,
                        "cgi_received_at": now,
                        "normalized_at": now,
                        "correlation_wait_ms": 0.2,
                    },
                }
            )

            control._record_observation(state, payload)

            self.assertEqual(state["messages"], 1)
            self.assertEqual(state["observations"], 1)
            self.assertTrue(state["last_observation"]["media"][0]["url"].startswith("/api/media/"))
            self.assertGreaterEqual(state["pipeline_latency_ms"], 0)
            self.assertGreaterEqual(
                state["latency_ms"]["event_age_at_dashboard"], 0
            )

    def test_common_contract_is_valid_json(self) -> None:
        contracts = COLLECTOR_ROOT.parents[2] / "contracts"
        observation_schema = json.loads(
            (contracts / "observation-v1.schema.json").read_text(encoding="utf-8")
        )
        track_schema = json.loads(
            (contracts / "track-update-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            observation_schema["properties"]["schema_version"]["const"],
            "observation.v1",
        )
        self.assertEqual(
            track_schema["properties"]["schema_version"]["const"],
            "track_update.v1",
        )

    def test_channel_health_and_bounded_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            control = ControlPlane(root / "cameras.json", root / ".env", output, 7, 3600)
            camera = CameraConfig(
                camera_id="cam-1",
                host="192.0.2.1",
                sdk_port=37777,
                http_port=80,
                username_env="CAM_USER",
                password_env="CAM_PASSWORD",
            )
            control.cameras[camera.camera_id] = camera
            control._initial_state(camera)

            control._handle_worker_message("cam-1", "worker_started")
            control._handle_worker_message("cam-1", "netsdk: Login succeeded")
            control._handle_worker_message("cam-1", "netsdk: Subscribed")
            control._handle_worker_message("cam-1", "status: CGI connected")
            control._handle_worker_message(
                "cam-1",
                "track_transport_connected topic=tracking/track-updates qos=1",
            )

            runtime = control.snapshot()["cameras"][0]["runtime"]
            self.assertEqual(runtime["status"], "running")
            self.assertEqual(runtime["channels"]["netsdk"], "running")
            self.assertEqual(runtime["channels"]["cgi"], "running")
            self.assertEqual(runtime["channels"]["transport"], "running")

            control._handle_worker_message(
                "cam-1", "warning: CGI disconnected: timed out"
            )
            runtime = control.snapshot()["cameras"][0]["runtime"]
            self.assertEqual(runtime["status"], "warning")
            self.assertEqual(runtime["channels"]["cgi"], "warning")

            for index in range(250):
                control._log("info", None, f"message {index}")
            self.assertEqual(len(control.snapshot()["logs"]), 200)
            control.clear_logs()
            self.assertEqual(control.snapshot()["logs"], [])


if __name__ == "__main__":
    unittest.main()
