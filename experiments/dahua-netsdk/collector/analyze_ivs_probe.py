#!/usr/bin/env python3
"""Summarize a dahua-ivs-probe session and optionally match it to CGI events.

Usage from the repository root:

    python experiments/dahua-netsdk/collector/analyze_ivs_probe.py \
        experiments/dahua-netsdk/output/ivs-probe/<session> \
        --cgi-log tests/dahua-events/output/<capture>.log

The report is read-only. It prints when live targets were present, which
camera-local track IDs appeared, and whether each ID also produced a CGI
HumanTrait event (the event that carries the native photos).
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from dahua_collector.ivs import (
    ONVIF_TYPE,
    TRACK_RECORD_TYPE,
    parse_onvif_frame,
    parse_track_record,
    presence_segments,
)


def load_session(session: Path) -> tuple[dict, list[dict]]:
    summary = json.loads((session / "summary.json").read_text(encoding="utf-8"))
    with (session / "ivs-frames.jsonl").open(encoding="utf-8") as stream:
        frames = [json.loads(line) for line in stream if line.strip()]
    return summary, frames


def track_spans(frames: list[dict]) -> "OrderedDict[int, dict]":
    spans: OrderedDict[int, dict] = OrderedDict()
    for frame in frames:
        if frame["type"] != TRACK_RECORD_TYPE or frame["encoding"] != "hex":
            continue
        record = parse_track_record(bytes.fromhex(frame["payload"]))
        seconds = frame["elapsed_ms"] / 1000
        span = spans.setdefault(
            record.track_id,
            {"first": seconds, "last": seconds, "frames": 0, "first_center_x": record.center_x},
        )
        span["last"] = seconds
        span["frames"] += 1
        span["last_center_x"] = record.center_x
    return spans


def onvif_presence(frames: list[dict]) -> list[float]:
    times = []
    for frame in frames:
        if frame["type"] != ONVIF_TYPE or frame["encoding"] != "hex":
            continue
        xml = bytes.fromhex(frame["payload"]).rstrip(b"\x00").decode("utf-8", "replace")
        if parse_onvif_frame(xml)[1]:
            times.append(frame["elapsed_ms"] / 1000)
    return times


def cgi_human_trait_ids(log: Path, started_at: str) -> dict[int, dict]:
    """Map CGI HumanTrait body/face ObjectIDs to their GroupID and RealUTC."""
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
    text = log.read_text(encoding="utf-8", errors="replace")
    result: dict[int, dict] = {}
    pattern = r"Code=HumanTrait;action=Start;index=\d+;data=(\{.*?\n\})"
    for match in re.finditer(pattern, text, re.S):
        data = json.loads(match.group(1))
        for item in data.get("Objects") or []:
            object_id = item.get("ObjectID")
            if isinstance(object_id, int):
                result[object_id] = {
                    "group_id": data.get("GroupID"),
                    "real_utc_seconds": round(data.get("RealUTC", start) - start, 1),
                    "belong_id": item.get("BelongID"),
                }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="probe session directory")
    parser.add_argument("--cgi-log", type=Path, help="simultaneous CGI attach capture")
    parser.add_argument("--gap", type=float, default=1.0,
                        help="seconds without targets that split presence (default 1.0)")
    args = parser.parse_args()

    summary, frames = load_session(args.session)
    print(f"camera={summary['camera_model']} firmware={summary['camera_firmware']} "
          f"stream={summary['stream']} duration={summary['duration_seconds']}s "
          f"ivs_frames={summary['ivs_frames']} dropped={summary['dropped_records']}")

    segments = presence_segments(onvif_presence(frames), args.gap)
    print("presence_segments_seconds=" +
          json.dumps([[round(start, 1), round(end, 1)] for start, end in segments]))

    cgi = cgi_human_trait_ids(args.cgi_log, summary["started_at"]) if args.cgi_log else {}
    for track_id, span in track_spans(frames).items():
        match = cgi.get(track_id)
        linked = (f"cgi_group={match['group_id']} cgi_real_utc={match['real_utc_seconds']}s"
                  if match else ("no CGI HumanTrait" if args.cgi_log else ""))
        print(f"track {track_id}: {span['first']:.1f}-{span['last']:.1f}s "
              f"frames={span['frames']} centre_x {span['first_center_x']}->"
              f"{span['last_center_x']} {linked}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
