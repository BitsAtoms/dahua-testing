#!/usr/bin/env python3
"""Resolve one controlled validation report into non-conflicting sequences."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from visual_reid.sequence_assignment import HandoffOption, resolve_sequences


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument(
        "--validation-database",
        type=Path,
        default=Path("runtime/space-mapper/validation.sqlite3"),
    )
    args = parser.parse_args()
    connection = sqlite3.connect(
        f"file:{args.validation_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        row = connection.execute(
            "SELECT report_json, annotations_json FROM validation_sessions WHERE session_id=?",
            (args.session_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("validation session not found")
    report = json.loads(row[0] or "{}")
    annotations = json.loads(row[1] or "{}")
    excluded_tracks = {
        track_id
        for track_id, classification in annotations.get(
            "track_classifications", {}
        ).items()
        if classification in {"false_positive", "out_of_scope_person"}
    }
    tracks = {
        item["track_id"]: item for item in report.get("tracks", [])
    }
    options = [
        HandoffOption(
            candidate_id=item["candidate_id"],
            origin_track_id=item["origin_track_id"],
            destination_track_id=item["destination_track_id"],
            score=float(item.get("visual_ranking") or 0.0),
            visual_coverage=float(item.get("visual_coverage") or 0.0),
            observed_us=int(item["observed_us"]),
            origin_first_observed_us=_timestamp_us(
                tracks.get(item["origin_track_id"], {}).get("first_observed_at")
            ),
            destination_first_observed_us=_timestamp_us(
                tracks.get(item["destination_track_id"], {}).get(
                    "first_observed_at"
                )
            ),
        )
        for item in report.get("candidates", [])
        if item["origin_track_id"] not in excluded_tracks
        and item["destination_track_id"] not in excluded_tracks
    ]
    resolution = resolve_sequences(options)
    print(
        f"sequence_resolution session={args.session_id} "
        f"options={len(options)} excluded_tracks={len(excluded_tracks)} "
        f"utility={resolution.total_utility:.3f}"
    )
    for index, sequence in enumerate(resolution.sequences, start=1):
        print(f"hypothesis={index} tracks={' -> '.join(sequence)}")
    for index, sequence in enumerate(resolution.confirmed_sequences, start=1):
        print(f"confirmed_sequence={index} tracks={' -> '.join(sequence)}")
    for assigned in sorted(
        resolution.handoffs,
        key=lambda item: item.option.observed_us,
    ):
        print(
            f"candidate={assigned.option.candidate_id} "
            f"state={assigned.state.value} utility={assigned.option.utility:.3f} "
            f"coverage={assigned.option.visual_coverage:.3f} "
            f"margin={assigned.margin:.3f} reason={assigned.reason}"
        )
    return 0


def _timestamp_us(value: str | None) -> int | None:
    if not value:
        return None
    return round(datetime.fromisoformat(value).timestamp() * 1_000_000)


if __name__ == "__main__":
    raise SystemExit(main())
