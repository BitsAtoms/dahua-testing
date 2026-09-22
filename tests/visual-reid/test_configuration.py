from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.configuration import merged_config, read_env  # noqa: E402


class ConfigurationTests(unittest.TestCase):
    def test_reads_quoted_values_without_exporting_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# ignored\nCAM_A='rtsp://example/a'\nCAM_B=rtsp://example/b\n",
                encoding="utf-8",
            )

            values = read_env(path)

            self.assertEqual(values["CAM_A"], "rtsp://example/a")
            self.assertEqual(values["CAM_B"], "rtsp://example/b")
            self.assertNotIn("CAM_A", os.environ)

    def test_process_environment_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("CAM_A=file-value\n", encoding="utf-8")
            with patch.dict(os.environ, {"CAM_A": "process-value"}):
                self.assertEqual(merged_config(path)["CAM_A"], "process-value")


if __name__ == "__main__":
    unittest.main()
