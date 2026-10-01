from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.console import SupervisorConsole, mask_secrets  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


class SupervisorConsoleTests(unittest.TestCase):
    def test_follows_new_complete_lines_of_the_newest_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "20261001T080000Z" / "supervisor.log", "2026-10-01T08:00:01+00:00 old session\n")
            log = root / "20261001T083524Z" / "supervisor.log"
            write(log, "2026-10-01T08:35:24+00:00 power keep_awake=on\n2026-10-01T08:35:25+00:00 [infra")
            console = SupervisorConsole(root)

            first = console.read()
            write(log, "structure] docker engine=ready\n")
            second = console.read(after=first["last_seq"])

            self.assertEqual(first["session"], "20261001T083524Z")
            self.assertEqual([line["text"] for line in first["lines"]], ["power keep_awake=on"])
            self.assertEqual([line["text"] for line in second["lines"]], ["[infrastructure] docker engine=ready"])
            self.assertEqual(second["lines"][0]["at"], "2026-10-01T08:35:25+00:00")
            self.assertEqual(second["last_seq"], 2)

    def test_a_new_session_is_read_from_its_start_and_numbering_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "20261001T080000Z" / "supervisor.log", "2026-10-01T08:00:01+00:00 first\n")
            console = SupervisorConsole(root)
            console.read()
            write(root / "20261001T090000Z" / "supervisor.log", "2026-10-01T09:00:01+00:00 second\n")

            data = console.read(after=1)

            self.assertEqual(data["session"], "20261001T090000Z")
            self.assertEqual([(line["seq"], line["text"]) for line in data["lines"]], [(2, "second")])

    def test_only_the_last_lines_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "20261001T080000Z" / "supervisor.log",
                  "".join(f"2026-10-01T08:00:00+00:00 line {n}\n" for n in range(10)))

            data = SupervisorConsole(root, keep=3).read()

            self.assertEqual([line["text"] for line in data["lines"]], ["line 7", "line 8", "line 9"])

    def test_a_long_log_is_read_from_near_its_end_without_a_broken_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "20261001T080000Z" / "supervisor.log",
                  "".join(f"2026-10-01T08:00:00+00:00 line {n:04d}\n" for n in range(1000)))

            data = SupervisorConsole(root, tail_bytes=200).read()

            texts = [line["text"] for line in data["lines"]]
            self.assertEqual(texts[-1], "line 0999")
            self.assertLess(len(texts), 10)
            self.assertTrue(all(text.startswith("line ") and len(text) == 9 for text in texts))

    def test_missing_log_root_is_an_empty_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = SupervisorConsole(Path(directory) / "missing").read()

        self.assertIsNone(data["session"])
        self.assertEqual(data["lines"], [])


class MaskSecretsTests(unittest.TestCase):
    def test_credentials_in_urls_and_key_values_are_hidden(self) -> None:
        line = 'go2rtc src=rtsp://admin:S3cret!@camera/stream password=hunter2 token: "abc def"'

        masked = mask_secrets(line)

        self.assertNotIn("S3cret", masked)
        self.assertNotIn("hunter2", masked)
        self.assertNotIn("abc def", masked)
        self.assertIn("rtsp://***@camera/stream", masked)

    def test_ordinary_lines_are_unchanged(self) -> None:
        line = "[infrastructure] frigate detector=zmq expected=gpu"

        self.assertEqual(mask_secrets(line), line)


if __name__ == "__main__":
    unittest.main()
