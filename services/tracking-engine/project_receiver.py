#!/usr/bin/env python3
"""Project the durable receiver log into local track state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKING_ROOT = Path(__file__).resolve().parent
RECEIVER_ROOT = REPOSITORY_ROOT / "services" / "track-receiver"
sys.path.insert(0, str(TRACKING_ROOT))
sys.path.insert(0, str(RECEIVER_ROOT))

from track_receiver import validate_track_update
from tracking_engine import TrackingStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("runtime/tracking-engine/tracking.sqlite3"),
    )
    args = parser.parse_args()

    source = sqlite3.connect(f"file:{args.receiver_database.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    applied = duplicates = 0
    try:
        with TrackingStore(args.database) as target:
            rows = source.execute(
                """
                SELECT payload_json, receiver_received_at
                FROM track_updates
                ORDER BY receiver_received_us, rowid
                """
            )
            for row in rows:
                update = json.loads(row["payload_json"])
                validate_track_update(update)
                result = target.project(
                    update,
                    received_at=_timestamp(row["receiver_received_at"]),
                )
                if result.applied:
                    applied += 1
                else:
                    duplicates += 1
            expired = target.expire_stale()
            removed = target.cleanup()
            print(
                f"projected={applied} duplicates={duplicates} "
                f"tracks={target.count()} expired={expired} cleanup={removed}"
            )
    finally:
        source.close()
    return 0


def _timestamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


if __name__ == "__main__":
    raise SystemExit(main())
