"""Camera inventory format and the camera_info parsing helpers."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sys
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "deploy" / "tools"))

import camera_info  # noqa: E402

EXAMPLE = REPOSITORY_ROOT / "deploy" / "camera-inventory.example.json"
LOCAL = REPOSITORY_ROOT / "deploy" / "camera-inventory.local.json"

REQUIRED = {"inventory_id", "camera_id", "brand", "model", "firmware", "ingestion",
            "live_position", "status", "space", "notes"}
INGESTION = {"dahua_collector", "frigate", "frigate_view_only", "not_integrated"}
LIVE_POSITION = {"ivs", "to_verify", "frigate", "none"}
STATUS = {"planned", "test", "installed"}


def inventory_errors(document: dict) -> list[str]:
    errors = []
    if document.get("schema_version") != "camera_inventory.v1":
        errors.append("schema_version")
    cameras = document.get("cameras", [])
    ids = [camera.get("inventory_id") for camera in cameras]
    if len(ids) != len(set(ids)):
        errors.append("duplicate inventory_id")
    connected = [camera["camera_id"] for camera in cameras if camera.get("camera_id")]
    if len(connected) != len(set(connected)):
        errors.append("duplicate camera_id")
    for camera in cameras:
        name = camera.get("inventory_id")
        if set(camera) != REQUIRED:
            errors.append(f"{name}: fields {sorted(set(camera) ^ REQUIRED)}")
            continue
        if camera["ingestion"] not in INGESTION:
            errors.append(f"{name}: ingestion")
        if camera["live_position"] not in LIVE_POSITION:
            errors.append(f"{name}: live_position")
        if camera["status"] not in STATUS:
            errors.append(f"{name}: status")
        if camera["status"] != "planned" and not camera["camera_id"]:
            errors.append(f"{name}: connected camera without camera_id")
        if camera["status"] != "installed" and camera["space"] is not None:
            errors.append(f"{name}: space before final placement")
    text = json.dumps(document)
    if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text) or re.search(r"\w+://", text):
        errors.append("addresses or URLs")
    return errors


class InventoryTests(unittest.TestCase):
    def test_example_is_valid_and_site_neutral(self) -> None:
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(inventory_errors(document), [])
        for camera in document["cameras"]:
            self.assertRegex(camera["model"] or "", r"EXAMPLE")

    def test_local_inventory_is_valid_when_present(self) -> None:
        if not LOCAL.exists():
            self.skipTest("no local inventory on this machine")
        document = json.loads(LOCAL.read_text(encoding="utf-8"))
        self.assertEqual(inventory_errors(document), [])

    def test_space_is_rejected_before_placement(self) -> None:
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        document["cameras"][0]["space"] = "Room A"
        self.assertIn("cam-01: space before final placement", inventory_errors(document))


class CameraInfoParsingTests(unittest.TestCase):
    def test_frigate_targets_group_main_and_sub_streams(self) -> None:
        env = {
            "FRIGATE_LOBBY_URL": "rtsp://user:p%40ss@192.0.2.10:554/Streaming/Channels/101",
            "FRIGATE_LOBBY_SUB_URL": "rtsp://user:p%40ss@192.0.2.10:554/Streaming/Channels/102",
            "FRIGATE_DOOR_SUB_URL": "rtsp://viewer:x@192.0.2.11/live0",
            "STACK_FRIGATE_CONFIG_DIR": "../../runtime/frigate/config",
        }
        targets = {target.camera_id: target for target in camera_info.frigate_targets(env)}
        self.assertEqual(set(targets), {"lobby", "door"})
        self.assertEqual(targets["lobby"].password, "p@ss")
        self.assertEqual(targets["door"].host, "192.0.2.11")

    def test_dahua_key_values(self) -> None:
        parsed = camera_info.parse_key_values("type=DH-IPC-X\r\nversion=3.1,build:2026-01-26\r\n")
        self.assertEqual(parsed, {"type": "DH-IPC-X", "version": "3.1,build:2026-01-26"})

    def test_isapi_xml_with_namespace(self) -> None:
        xml = ('<DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">'
               "<model>iDS-EXAMPLE</model><firmwareVersion>V1.0</firmwareVersion></DeviceInfo>")
        self.assertEqual(camera_info.parse_isapi(xml)["model"], "iDS-EXAMPLE")

    def test_clock_offset_accepts_both_camera_formats(self) -> None:
        now = datetime(2026, 9, 30, 12, 0, 0)
        self.assertEqual(camera_info.clock_offset_seconds("2026-09-30 12:00:03", now), 3.0)
        self.assertEqual(camera_info.clock_offset_seconds("2026-09-30T11:59:57+02:00", now), -3.0)
        self.assertIsNone(camera_info.clock_offset_seconds("garbage", now))


if __name__ == "__main__":
    unittest.main()
