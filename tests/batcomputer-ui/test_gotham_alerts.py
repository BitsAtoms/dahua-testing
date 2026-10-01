from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALERTS = REPOSITORY_ROOT / "services/batcomputer-ui/web/data/gotham-alerts.json"
STATES = {"critical", "error", "warning", "unknown", "ok"}
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class GothamAlertsTests(unittest.TestCase):
    """The owner edits this file by hand: keep it valid for web/gotham.js."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(ALERTS.read_text(encoding="utf-8"))

    def test_weights_cover_every_state_and_every_state_has_messages(self) -> None:
        self.assertEqual(set(self.data["weights"]), STATES)
        self.assertTrue(all(weight > 0 for weight in self.data["weights"].values()))
        present = {message["state"] for message in self.data["messages"]}
        self.assertEqual(present, STATES)

    def test_messages_are_short_and_well_formed(self) -> None:
        for message in self.data["messages"]:
            with self.subTest(message=message["label"]):
                self.assertEqual(set(message), {"state", "label", "value"})
                self.assertIn(message["state"], STATES)
                self.assertTrue(message["label"].strip())
                # One row on a 15" screen: label and value must fit.
                self.assertLessEqual(len(message["label"]) + len(message["value"]), 60)

    def test_placeholders_are_ranges_or_choices(self) -> None:
        for message in self.data["messages"]:
            for text in (message["label"], message["value"]):
                self.assertEqual(text.count("{"), text.count("}"), text)
                for body in PLACEHOLDER.findall(text):
                    with self.subTest(text=text, placeholder=body):
                        number_range = re.fullmatch(r"(-?\d+)-(-?\d+)", body)
                        if number_range:
                            self.assertLessEqual(int(number_range[1]), int(number_range[2]))
                        else:
                            self.assertGreater(len(body.split("|")), 1)


if __name__ == "__main__":
    unittest.main()
