"""Adapters from Dahua source payloads to correlation records."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Iterable


_EVENT_PREFIX = re.compile(
    r"Code=HumanTrait;action=(?P<action>[^;]+);index=(?P<index>\d+);data="
)


class CgiHumanTraitStreamParser:
    """Incrementally parse pretty-printed HumanTrait JSON from the CGI stream."""

    def __init__(self) -> None:
        self._decoder = json.JSONDecoder()
        self._action: str | None = None
        self._index: int | None = None
        self._json_text = ""

    def feed_line(self, line: str) -> list[dict[str, Any]]:
        if self._action is None:
            match = _EVENT_PREFIX.search(line)
            if match is None:
                return []
            self._action = match.group("action")
            self._index = int(match.group("index"))
            self._json_text = line[match.end() :] + "\n"
        else:
            self._json_text += line + "\n"

        try:
            payload, _ = self._decoder.raw_decode(self._json_text.lstrip())
        except json.JSONDecodeError:
            return []

        records = cgi_payload_to_records(self._action, self._index, payload)
        self._action = None
        self._index = None
        self._json_text = ""
        return records


def cgi_payload_to_records(
    action: str, index: int, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    records = []
    for detected_object in payload.get("Objects") or []:
        if not isinstance(detected_object, dict):
            continue
        records.append(
            {
                "action": action,
                "index": index,
                "object_type": detected_object.get("ObjectType"),
                "group_id": payload.get("GroupID"),
                "object_id": detected_object.get("ObjectID"),
                "relative_id": detected_object.get("RelativeID"),
                "belong_id": detected_object.get("BelongID"),
                "event_id": payload.get("EventID"),
                "event_uuid": payload.get("EventUUIDStr"),
                "frame_sequence": detected_object.get(
                    "FrameSequence", payload.get("FrameSequence")
                ),
                "timestamp": payload.get("RealUTC", payload.get("UTC")),
                "bounding_box": deepcopy(detected_object.get("BoundingBox")),
                "center": deepcopy(detected_object.get("Center")),
                "with_snapshot": bool(payload.get("WithSnap")),
                "attributes": deepcopy(detected_object),
                "raw": deepcopy(payload),
            }
        )
    return records


def parse_cgi_recording(path: Path) -> Iterable[dict[str, Any]]:
    parser = CgiHumanTraitStreamParser()
    with path.open(encoding="utf-8", errors="replace") as recording:
        for line in recording:
            yield from parser.feed_line(line.rstrip("\r\n"))


def load_netsdk_recording(directory: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(directory.glob("*_event.json")):
        yield load_netsdk_event(path)


def load_netsdk_event(path: Path) -> dict[str, Any]:
    path = path.resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_event_file"] = str(path)
    for snapshot in payload.get("snapshots") or []:
        filename = snapshot.get("file")
        if filename and not Path(filename).is_absolute():
            snapshot["file"] = str((path.parent / filename).resolve())
    return payload
