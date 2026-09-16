#!/usr/bin/env python3
"""Project the durable receiver log into local track state."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKING_ROOT = Path(__file__).resolve().parent
RECEIVER_ROOT = REPOSITORY_ROOT / "services" / "track-receiver"
sys.path.insert(0, str(TRACKING_ROOT))
sys.path.insert(0, str(RECEIVER_ROOT))

from track_receiver import validate_track_update
from tracking_engine import TrackingRunner, TrackingStore


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

    applied = duplicates = 0
    with TrackingStore(args.database) as target:
        runner = TrackingRunner(
            args.receiver_database, target, validate_track_update
        )
        while True:
            batch = runner.poll()
            applied += batch.applied
            duplicates += batch.duplicates
            if batch.scanned < runner.batch_size:
                break
        expired = target.expire_stale()
        removed = target.cleanup()
        print(
            f"projected={applied} duplicates={duplicates} "
            f"tracks={target.count()} expired={expired} cleanup={removed}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
