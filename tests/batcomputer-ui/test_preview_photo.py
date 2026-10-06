"""The preview's photo view: every screen has a display in the photo."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.screens import SCREENS  # noqa: E402


MAPPING = REPOSITORY_ROOT / "services" / "batcomputer-ui" / "web" / "data" / "photo-screens.json"


class PhotoMappingTests(unittest.TestCase):
    def test_every_screen_has_four_corners_inside_the_photo(self) -> None:
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        width, height = mapping["photo"]["width"], mapping["photo"]["height"]

        self.assertEqual(set(mapping["screens"]), set(SCREENS))
        for position_id, quad in mapping["screens"].items():
            with self.subTest(screen=position_id):
                self.assertEqual(len(quad), 4)
                for x, y in quad:
                    self.assertTrue(0 <= x <= width and 0 <= y <= height)
                # Clockwise from the top-left corner, as the preview expects.
                area = sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1]
                           for i in range(4))
                self.assertGreater(area, 0)

    def test_the_photo_itself_is_never_committed(self) -> None:
        photo = "services/batcomputer-ui/web/local/batcomputer.png"
        ignored = subprocess.run(["git", "check-ignore", "-q", photo], cwd=REPOSITORY_ROOT)
        self.assertEqual(ignored.returncode, 0)


if __name__ == "__main__":
    unittest.main()
