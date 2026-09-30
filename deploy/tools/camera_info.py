#!/usr/bin/env python3
"""Read model, firmware and clock of the configured cameras (read only).

Sources, all ignored by Git:
- Dahua: experiments/dahua-netsdk/cameras.local.json plus the repository .env
  holding the credentials it names; queried through the Dahua CGI.
- Frigate cameras: the FRIGATE_<CAMERA>[_SUB]_URL variables in
  deploy/docker/.env; queried through Hikvision ISAPI when available.

Output never contains addresses, credentials or serial numbers.

  python deploy\\tools\\camera_info.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

REPOSITORY = Path(__file__).resolve().parents[2]
DAHUA_CAMERAS = REPOSITORY / "experiments" / "dahua-netsdk" / "cameras.local.json"
REPOSITORY_ENV = REPOSITORY / ".env"
STACK_ENV = REPOSITORY / "deploy" / "docker" / ".env"
TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class Target:
    camera_id: str
    host: str
    http_port: int
    username: str
    password: str
    protocol: str  # "dahua" or "frigate"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("'\"")
    return values


def frigate_targets(stack_env: dict[str, str]) -> list[Target]:
    """One target per Frigate camera, from its first stream URL."""
    targets: dict[str, Target] = {}
    for name, value in stack_env.items():
        match = re.fullmatch(r"FRIGATE_([A-Z0-9_]+?)(?:_SUB)?_URL", name)
        if not match or not value.startswith("rtsp://"):
            continue
        camera_id = match.group(1).lower()
        url = urllib.parse.urlsplit(value)
        if camera_id not in targets and url.hostname:
            targets[camera_id] = Target(
                camera_id, url.hostname, 80,
                urllib.parse.unquote(url.username or ""), urllib.parse.unquote(url.password or ""),
                "frigate",
            )
    return list(targets.values())


def dahua_targets(config: dict, repository_env: dict[str, str]) -> list[Target]:
    return [
        Target(
            camera["camera_id"], camera["host"], int(camera.get("http_port", 80)),
            repository_env.get(camera["username_env"], ""), repository_env.get(camera["password_env"], ""),
            "dahua",
        )
        for camera in config.get("cameras", [])
        if camera.get("enabled", True)
    ]


def parse_key_values(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.strip().splitlines() if "=" in line)


def parse_isapi(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {element.tag.split("}")[-1]: (element.text or "").strip() for element in root}


def clock_offset_seconds(camera_time: str, now: datetime) -> float | None:
    """Offset of a camera's local wall clock, assuming the PC's time zone."""
    cleaned = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", camera_time.strip()).replace("T", " ")
    try:
        parsed = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return round((parsed - now.replace(tzinfo=None, microsecond=0)).total_seconds(), 1)


def get(target: Target, path: str) -> str:
    base = f"http://{target.host}:{target.http_port}"
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, base, target.username, target.password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager), urllib.request.HTTPBasicAuthHandler(manager)
    )
    with opener.open(base + path, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def describe(target: Target) -> dict[str, object]:
    row: dict[str, object] = {"camera_id": target.camera_id}
    try:
        if target.protocol == "dahua":
            row["brand"] = "Dahua"
            row["model"] = parse_key_values(get(target, "/cgi-bin/magicBox.cgi?action=getDeviceType")).get("type")
            row["firmware"] = parse_key_values(get(target, "/cgi-bin/magicBox.cgi?action=getSoftwareVersion")).get("version")
            camera_time = parse_key_values(get(target, "/cgi-bin/global.cgi?action=getCurrentTime")).get("result", "")
        else:
            info = parse_isapi(get(target, "/ISAPI/System/deviceInfo"))
            row["brand"] = "Hikvision"
            row["model"] = info.get("model")
            row["firmware"] = " ".join(filter(None, [info.get("firmwareVersion"), info.get("firmwareReleasedDate")]))
            camera_time = parse_isapi(get(target, "/ISAPI/System/time")).get("localTime", "")
        row["clock_offset_s"] = clock_offset_seconds(camera_time, datetime.now())
    except urllib.error.HTTPError as error:
        row["error"] = f"HTTP {error.code} (no {'Dahua CGI' if target.protocol == 'dahua' else 'ISAPI'} access)"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        row["error"] = f"unreachable ({type(getattr(error, 'reason', error)).__name__})"
    except ET.ParseError:
        row["error"] = "not an ISAPI device"
    return row


def main() -> int:
    targets = []
    if DAHUA_CAMERAS.exists():
        targets += dahua_targets(json.loads(DAHUA_CAMERAS.read_text(encoding="utf-8")), read_env(REPOSITORY_ENV))
    dahua_ids = {target.camera_id for target in targets}
    targets += [target for target in frigate_targets(read_env(STACK_ENV)) if target.camera_id not in dahua_ids]
    for target in targets:
        print(json.dumps(describe(target), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
