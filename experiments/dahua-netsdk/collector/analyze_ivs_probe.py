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
from dahua_collector.live_lane import DahuaLiveLane, LiveTarget


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


def replay_lifecycle(frames: list[dict], session_id: str) -> None:
    """Replay recorded targets through the live lane using PC receipt time."""
    lane = DahuaLiveLane("replay", session_id, clock=lambda: 0.0)
    counts: dict[str, int] = {}
    started = None
    for frame in frames:
        received = datetime.fromisoformat(frame["received_at"].replace("Z", "+00:00")).timestamp()
        started = received if started is None else started
        for message in lane.expire(received):
            report_lifecycle(message, started, counts)
        if frame["type"] != TRACK_RECORD_TYPE or frame["encoding"] != "hex":
            continue
        record = parse_track_record(bytes.fromhex(frame["payload"]))
        target = LiveTarget(record.track_id, record.box, received, frame["frame_sequence"])
        for message in lane.ingest(target):
            report_lifecycle(message, started, counts)
    for message in lane.close("replay_finished"):
        report_lifecycle(message, started, counts)
    print(f"lifecycle_messages={json.dumps(counts, sort_keys=True)} "
          f"replayed_frames_dropped={lane.stats.replayed_frames_dropped}")


def report_lifecycle(message: dict, started: float | None, counts: dict[str, int]) -> None:
    counts[message["phase"]] = counts.get(message["phase"], 0) + 1
    if message["phase"] in {"new", "end"}:
        observed = datetime.fromisoformat(message["observed_at"]).timestamp()
        reason = message["quality"].get("end_reason", "")
        print(f"  {message['phase']:<3} object={message['subject']['local_track_id']} "
              f"at={observed - (started or observed):.1f}s {reason}".rstrip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="probe session directory")
    parser.add_argument("--cgi-log", type=Path, help="simultaneous CGI attach capture")
    parser.add_argument("--gap", type=float, default=1.0,
                        help="seconds without targets that split presence (default 1.0)")
    parser.add_argument("--lifecycle", action="store_true",
                        help="replay targets through the collector's live lane "
                             "(silence fallback only: the CGI capture has no receipt "
                             "times to replay HumanTrait finalization)")
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
    if args.lifecycle:
        print("live lane replay:")
        replay_lifecycle(frames, args.session.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
