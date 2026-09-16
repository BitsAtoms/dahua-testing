from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_ROOT = REPOSITORY_ROOT / "services" / "local-supervisor"
sys.path.insert(0, str(SUPERVISOR_ROOT))

from local_supervisor.configuration import (  # noqa: E402
    SERVICE_NAMES,
    build_specs,
    load_config,
)
from local_supervisor.retention import cleanup_logs  # noqa: E402


class ConfigurationTests(unittest.TestCase):
    def test_missing_default_file_enables_complete_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(root / "local-stack.json", root)

        self.assertEqual(set(config.enabled), set(SERVICE_NAMES))
        self.assertTrue(all(config.enabled.values()))
        self.assertEqual(config.visual_device, "CPU")

    def test_local_file_can_disable_one_fixed_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "custom.json"
            path.write_text(
                json.dumps({"services": {"frigate_adapter": False}}),
                encoding="utf-8",
            )
            config = load_config(path, root)
            specs = build_specs(config, REPOSITORY_ROOT)

        self.assertNotIn("frigate_adapter", [spec.name for spec in specs])
        self.assertEqual(len(specs), len(SERVICE_NAMES) - 1)

    def test_unknown_service_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "custom.json"
            path.write_text(
                json.dumps({"services": {"arbitrary_command": True}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown services"):
                load_config(path, root)

    def test_non_boolean_service_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "custom.json"
            path.write_text(
                json.dumps({"services": {"visual_reid": "yes"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                load_config(path, root)

    def test_supervisor_logs_follow_seven_day_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "old-session"
            session.mkdir()
            old_log = session / "worker.log"
            old_log.write_text("old", encoding="utf-8")
            current = datetime(2026, 9, 16, tzinfo=timezone.utc)
            old_timestamp = (current - timedelta(days=8)).timestamp()
            os.utime(old_log, (old_timestamp, old_timestamp))

            removed_files, removed_bytes = cleanup_logs(root, current)

            self.assertEqual((removed_files, removed_bytes), (1, 3))
            self.assertFalse(old_log.exists())
            self.assertFalse(session.exists())


if __name__ == "__main__":
    unittest.main()
