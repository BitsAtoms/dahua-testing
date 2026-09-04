#!/usr/bin/env python3
"""Compare Dahua CGI HumanTrait metadata with NetSDK event JSON files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


EVENT_RE = re.compile(
    r"Code=HumanTrait;action=(?P<action>[^;]+);index=(?P<index>\d+);data="
)
INTERESTING_KEYS = {
    "ObjectID",
    "BelongID",
    "RelativeID",
    "EventID",
    "EventUUIDStr",
    "GroupID",
    "FrameSequence",
    "UTC",
    "RealUTC",
    "ObjectType",
    "WithSnap",
}


def walk(value: Any, path: str = "data") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in INTERESTING_KEYS:
                yield child_path, key, child
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def parse_cgi(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    for match in EVENT_RE.finditer(text):
        line_number = text.count("\n", 0, match.start()) + 1
        payload_start = match.end()
        try:
            payload, _ = decoder.raw_decode(text, payload_start)
        except json.JSONDecodeError as error:
            events.append(
                {
                    "line": line_number,
                    "action": match.group("action"),
                    "parse_error": str(error),
                }
            )
            continue
        fields = [
            {"path": field_path, "key": key, "value": value}
            for field_path, key, value in walk(payload)
        ]
        events.append(
            {
                "line": line_number,
                "action": match.group("action"),
                "index": int(match.group("index")),
                "fields": fields,
            }
        )
    return events


def parse_netsdk(directory: Path) -> list[dict[str, Any]]:
    events = []
    for path in sorted(directory.glob("*_event.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_file"] = path.name
        events.append(payload)
    return events


def compact_fields(fields: list[dict[str, Any]]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for field in fields:
        values = result.setdefault(field["key"], [])
        if field["value"] not in values:
            values.append(field["value"])
    return result


def values_for(event: dict[str, Any], key: str) -> list[Any]:
    return [
        field["value"]
        for field in event.get("fields", [])
        if field["key"] == key
    ]


def first_value(event: dict[str, Any], key: str) -> Any | None:
    values = values_for(event, key)
    return values[0] if values else None


def related_faces(
    body_event: dict[str, Any], starts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    body_id = first_value(body_event, "ObjectID")
    face_ids = set(values_for(body_event, "RelativeID")) - {0, None}
    related = []

    for event in starts:
        if "HumanFace" not in values_for(event, "ObjectType"):
            continue
        face_id = first_value(event, "ObjectID")
        belongs_to = set(values_for(event, "BelongID"))
        relative_to = set(values_for(event, "RelativeID"))
        if face_id in face_ids or body_id in belongs_to or body_id in relative_to:
            related.append(
                {
                    "group_id": first_value(event, "GroupID"),
                    "event_id": first_value(event, "EventID"),
                    "object_id": face_id,
                    "belong_id": first_value(event, "BelongID"),
                    "relative_id": first_value(event, "RelativeID"),
                    "frame_sequences": values_for(event, "FrameSequence"),
                }
            )
    return related


def correlate(
    cgi_events: list[dict[str, Any]], netsdk_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    starts = [
        event
        for event in cgi_events
        if event.get("action") == "Start" and not event.get("parse_error")
    ]
    matches = []
    for netsdk in netsdk_events:
        group_id = netsdk.get("group_id")
        candidates = [
            event for event in starts if group_id in values_for(event, "GroupID")
        ]
        if len(candidates) != 1:
            matches.append(
                {
                    "netsdk_file": netsdk.get("_file"),
                    "group_id": group_id,
                    "status": "unmatched" if not candidates else "ambiguous",
                    "candidate_count": len(candidates),
                }
            )
            continue

        cgi = candidates[0]
        matches.append(
            {
                "netsdk_file": netsdk.get("_file"),
                "status": "matched_by_group_id",
                "group_id": group_id,
                "cgi_event_id": values_for(cgi, "EventID"),
                "cgi_event_uuid": values_for(cgi, "EventUUIDStr"),
                "cgi_object_ids": values_for(cgi, "ObjectID"),
                "cgi_belong_ids": values_for(cgi, "BelongID"),
                "cgi_relative_ids": values_for(cgi, "RelativeID"),
                "cgi_frame_sequences": values_for(cgi, "FrameSequence"),
                "related_face_events": related_faces(cgi, starts),
                "netsdk_event_uuid": netsdk.get("event_uuid"),
                "snapshots": netsdk.get("snapshots", []),
            }
        )
    return matches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cgi_log", type=Path)
    parser.add_argument("netsdk_output", type=Path)
    args = parser.parse_args()

    cgi_events = parse_cgi(args.cgi_log)
    netsdk_events = parse_netsdk(args.netsdk_output)

    report = {
        "cgi_humantrait_count": len(cgi_events),
        "netsdk_humantrait_count": len(netsdk_events),
        "correlations": correlate(cgi_events, netsdk_events),
        "cgi_events": [
            {
                "line": event.get("line"),
                "action": event.get("action"),
                "parse_error": event.get("parse_error"),
                "fields": compact_fields(event.get("fields", [])),
            }
            for event in cgi_events
        ],
        "netsdk_events": [
            {
                key: event.get(key)
                for key in (
                    "_file",
                    "timestamp",
                    "action",
                    "local_track_id",
                    "event_id",
                    "group_id",
                    "event_uuid",
                    "unique_id",
                    "object_uuid",
                    "serial_uuid",
                    "start_sequence",
                    "end_sequence",
                    "detect_object",
                    "snapshots",
                )
            }
            for event in netsdk_events
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
