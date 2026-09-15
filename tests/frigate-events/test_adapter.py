from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ROOT = REPOSITORY_ROOT / "experiments" / "frigate-adapter"
sys.path.insert(0, str(ADAPTER_ROOT))

from frigate_adapter import FrigateEventAdapter  # noqa: E402
from frigate_adapter.retention import remove_expired_files  # noqa: E402
from frigate_adapter.snapshots import (  # noqa: E402
    SnapshotFetchError,
    fetch_snapshot,
    validate_api_url,
)
from frigate_adapter.timing import build_pipeline_timing  # noqa: E402
from mqtt_runner import parse_frame_sizes  # noqa: E402


class FrigateEventAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = Path(__file__).parent / "fixtures" / "person-lifecycle.jsonl"
        cls.payloads = [
            json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()
        ]

    def setUp(self) -> None:
        self.adapter = FrigateEventAdapter(
            {"front_door": (1280, 720)}, instance_id="local-frigate"
        )

    def test_maps_complete_lifecycle_to_one_stable_track(self) -> None:
        updates = [
            self.adapter.adapt(payload, "2026-09-14T18:00:00+00:00")
            for payload in self.payloads
        ]

        self.assertEqual([item["phase"] for item in updates], ["new", "update", "end"])
        self.assertEqual(len({item["track_id"] for item in updates}), 1)
        self.assertEqual(len({item["message_id"] for item in updates}), 3)
        self.assertEqual(
            [item["sequence"] for item in updates],
            sorted(item["sequence"] for item in updates),
        )
        self.assertTrue(all(item["quality"]["source_lifecycle"] == "live" for item in updates))

    def test_normalizes_pixel_geometry_and_preserves_zones(self) -> None:
        update = self.adapter.adapt(self.payloads[1])

        self.assertEqual(update["geometry"]["coordinate_space"], "normalized_0_1")
        self.assertEqual(update["geometry"]["box"]["x_min"], 0.3)
        self.assertEqual(update["geometry"]["box"]["y_max"], 0.9)
        self.assertEqual(update["geometry"]["center"], {"x": 0.4, "y": 0.5})
        self.assertEqual(update["zones"]["current"], ["showroom"])
        self.assertEqual(update["zones"]["entered"], ["entrance", "showroom"])

    def test_message_id_is_idempotent_for_redelivery(self) -> None:
        first = self.adapter.adapt(self.payloads[1])
        duplicate = self.adapter.adapt(self.payloads[1])

        self.assertEqual(first["message_id"], duplicate["message_id"])
        self.assertEqual(first["sequence"], duplicate["sequence"])

    def test_missing_dimensions_emits_partial_update(self) -> None:
        update = FrigateEventAdapter({}).adapt(self.payloads[0])

        self.assertIsNone(update["geometry"])
        self.assertEqual(update["quality"]["status"], "partial")
        self.assertEqual(
            update["quality"]["issues"], ["missing_camera_frame_size"]
        )

    def test_ignores_non_person_and_false_positive(self) -> None:
        car = deepcopy_payload(self.payloads[0])
        car["after"]["label"] = "car"
        false_positive = deepcopy_payload(self.payloads[0])
        false_positive["after"]["false_positive"] = True

        self.assertIsNone(self.adapter.adapt(car))
        self.assertIsNone(self.adapter.adapt(false_positive))

    def test_matches_required_contract_fields(self) -> None:
        update = self.adapter.adapt(self.payloads[0])
        schema = json.loads(
            (REPOSITORY_ROOT / "contracts" / "track-update-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(set(update), set(schema["required"]))
        self.assertIn(update["phase"], schema["properties"]["phase"]["enum"])

    def test_parses_multiple_camera_frame_sizes(self) -> None:
        sizes = parse_frame_sizes("front_door=1280x720,dahua_213=704x576")

        self.assertEqual(sizes["front_door"], (1280, 720))
        self.assertEqual(sizes["dahua_213"], (704, 576))

    def test_retention_removes_only_files_older_than_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.jsonl"
            recent = root / "recent.jsonl"
            old.write_text("old", encoding="utf-8")
            recent.write_text("recent", encoding="utf-8")
            os.utime(old, (0, 0))
            now = 8 * 86400
            os.utime(recent, (now, now))

            removed_files, removed_bytes = remove_expired_files(root, now=now)

            self.assertEqual((removed_files, removed_bytes), (1, 3))
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_builds_separate_pipeline_timing_without_changing_update(self) -> None:
        update = self.adapter.adapt(
            self.payloads[0], "2026-09-14T14:00:00.352000+00:00"
        )

        timing = build_pipeline_timing(
            update,
            mqtt_received_at="2026-09-14T14:00:00.350000+00:00",
            output_persisted_at="2026-09-14T14:00:00.353000+00:00",
            received_ns=1_000_000_000,
            dequeued_ns=1_001_250_000,
            decoded_ns=1_001_500_000,
            raw_persisted_ns=1_001_800_000,
            normalize_started_ns=1_001_800_000,
            normalized_ns=1_002_550_000,
            output_persisted_ns=1_003_000_000,
        )

        self.assertEqual(timing["schema_version"], "pipeline_timing.v1")
        self.assertEqual(timing["message_id"], update["message_id"])
        self.assertEqual(
            timing["duration_ms"],
            {
                "event_age_at_mqtt": 250.0,
                "queue_wait": 1.25,
                "json_decode": 0.25,
                "raw_persist": 0.3,
                "normalize": 0.75,
                "update_persist": 0.45,
                "mqtt_callback_to_output": 3.0,
            },
        )
        self.assertNotIn("timing", update)

    def test_builds_correlated_snapshot_update(self) -> None:
        lifecycle = self.adapter.adapt(self.payloads[-1])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.jpg"
            update = self.adapter.snapshot_update(
                lifecycle,
                path,
                1789394401.5,
                "2026-09-14T14:00:02+00:00",
            )

        self.assertEqual(update["phase"], "snapshot")
        self.assertEqual(update["track_id"], lifecycle["track_id"])
        self.assertEqual(update["source_ref"]["event_id"], "fixture-person-001")
        self.assertEqual(update["media"][0]["role"], "snapshot")
        self.assertEqual(update["media"][0]["content_type"], "image/jpeg")
        self.assertNotEqual(update["message_id"], lifecycle["message_id"])

    def test_fetches_and_validates_jpeg_snapshot(self) -> None:
        response = FakeResponse(b"\xff\xd8\xffpayload\xff\xd9", "image/jpeg")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "event.jpg"
            with patch("frigate_adapter.snapshots.urlopen", return_value=response):
                byte_count = fetch_snapshot(
                    "http://127.0.0.1:5000", "event/with spaces", destination
                )

            self.assertEqual(byte_count, 12)
            self.assertEqual(destination.read_bytes(), b"\xff\xd8\xffpayload\xff\xd9")

    def test_rejects_credentials_and_invalid_snapshot(self) -> None:
        with self.assertRaises(ValueError):
            validate_api_url("http://user:password@127.0.0.1:5000")
        response = FakeResponse(b"not-a-jpeg", "image/jpeg")
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("frigate_adapter.snapshots.urlopen", return_value=response),
                patch("frigate_adapter.snapshots.time.sleep"),
                self.assertRaises(SnapshotFetchError),
            ):
                fetch_snapshot(
                    "http://127.0.0.1:5000",
                    "event",
                    Path(directory) / "event.jpg",
                    attempts=2,
                )


def deepcopy_payload(payload: dict) -> dict:
    return json.loads(json.dumps(payload))


class FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class FakeResponse:
    def __init__(self, data: bytes, content_type: str) -> None:
        self._data = data
        self.headers = FakeHeaders(content_type)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self._data


if __name__ == "__main__":
    unittest.main()
