"""Guards for the versioned Docker stack template.

The repository is public, so the template must never carry site data, and the
stack must stay bound to Windows loopback. These checks are text based because
the test environment has no YAML parser.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STACK_ROOT = REPOSITORY_ROOT / "deploy" / "docker"
TEMPLATE = STACK_ROOT / "frigate" / "config.template.yml"
COMPOSE = STACK_ROOT / "compose.yml"
ENV_EXAMPLE = STACK_ROOT / "env.example"

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
PLACEHOLDER = re.compile(r"\{(FRIGATE_[A-Z0-9_]+)\}")
RESTREAM = re.compile(r"rtsp://127\.0\.0\.1:8554/(\w+)")


def top_level_block(text: str, key: str) -> str:
    match = re.search(rf"^{key}:\n((?:[ #].*\n|\n)*)", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing top-level key {key!r}")
    return match.group(1)


class FrigateTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = TEMPLATE.read_text(encoding="utf-8")
        self.env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_template_has_no_site_addresses_or_credentials(self) -> None:
        addresses = set(IPV4.findall(self.template))
        self.assertLessEqual(addresses, {"127.0.0.1"})
        self.assertNotRegex(self.template, r"\w+://[^\s/'\"]+@")

    def test_cameras_read_only_local_go2rtc_relays(self) -> None:
        paths = re.findall(r"^\s+- path: (\S+)$", self.template, re.MULTILINE)
        self.assertTrue(paths)
        for path in paths:
            self.assertRegex(path, RESTREAM)

        go2rtc = top_level_block(self.template, "go2rtc")
        streams = set(re.findall(r"^    (\w+):$", go2rtc, re.MULTILINE))
        self.assertLessEqual(set(RESTREAM.findall(self.template)), streams)

    def test_every_placeholder_is_declared_in_env_example(self) -> None:
        used = set(PLACEHOLDER.findall(self.template))
        declared = set(
            re.findall(r"^(FRIGATE_[A-Z0-9_]+)=", self.env_example, re.MULTILINE)
        )
        self.assertTrue(used)
        self.assertEqual(used, declared)

    def test_env_example_uses_only_placeholder_hosts(self) -> None:
        self.assertEqual(IPV4.findall(self.env_example), [])
        for url in re.findall(r"rtsp://\S+", self.env_example):
            self.assertIn("USER:PASSWORD@192.168.1.XXX", url)

    def test_config_version_matches_pinned_frigate_image(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        image = re.search(r"frigate:(\d+)\.(\d+)\.\d+", compose)
        version = re.search(r"^version: (\d+)\.(\d+)-\d+$", self.template, re.MULTILINE)
        self.assertIsNotNone(image)
        self.assertIsNotNone(version)
        self.assertEqual(image.groups(), version.groups())

    def test_event_clips_only_with_seven_day_retention(self) -> None:
        record = top_level_block(self.template, "record")
        self.assertIn("enabled: true", record)
        self.assertEqual(
            re.findall(r"^  (?:continuous|motion):\n    days: (\S+)$", record, re.MULTILINE),
            ["0", "0"],
        )
        self.assertEqual(re.findall(r"^      days: (\S+)$", record, re.MULTILINE), ["7", "7"])

    def test_face_recognition_is_disabled(self) -> None:
        block = top_level_block(self.template, "face_recognition")
        self.assertIn("enabled: false", block)


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = COMPOSE.read_text(encoding="utf-8")

    def test_ports_listen_on_loopback_only(self) -> None:
        ports = re.findall(r'^\s+- "([^"]+)"$', self.compose, re.MULTILINE)
        self.assertTrue(ports)
        for port in ports:
            self.assertTrue(port.startswith("127.0.0.1:"), port)
        self.assertNotRegex(self.compose, r"network_mode:\s*host")

    def test_images_are_pinned_to_exact_versions(self) -> None:
        images = re.findall(r"^\s+image:\s*(\S+)$", self.compose, re.MULTILINE)
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertRegex(image, r":\d+\.\d+\.\d+(?:-[a-z]+)?$", image)

    def test_compose_has_no_site_data(self) -> None:
        addresses = set(IPV4.findall(self.compose))
        self.assertLessEqual(addresses, {"127.0.0.1"})
        self.assertNotRegex(self.compose, r"\w+://")


if __name__ == "__main__":
    unittest.main()
