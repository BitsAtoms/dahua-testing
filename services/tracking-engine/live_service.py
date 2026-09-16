#!/usr/bin/env python3
"""Continuously project receiver messages into provider-neutral track state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import signal
import sys
import threading
import time

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
    parser.add_argument("--poll-ms", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--status-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if args.poll_ms <= 0:
        raise ValueError("poll-ms must be positive")
    if args.status_seconds <= 0:
        raise ValueError("status-seconds must be positive")

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    with TrackingStore(args.database) as store:
        runner = TrackingRunner(
            args.receiver_database,
            store,
            validate_track_update,
            batch_size=args.batch_size,
        )
        print(
            f"tracking_engine_started receiver={args.receiver_database} "
            f"database={args.database} poll_ms={args.poll_ms}"
        )
        next_maintenance = time.monotonic()
        next_status = time.monotonic()
        try:
            while not stop.is_set():
                batch = runner.poll()
                now = time.monotonic()
                if batch.scanned:
                    print(
                        f"tracking_batch scanned={batch.scanned} "
                        f"applied={batch.applied} duplicates={batch.duplicates} "
                        f"cursor={batch.last_rowid}"
                    )
                if now >= next_maintenance:
                    expired = store.expire_stale()
                    removed = store.cleanup()
                    if expired or removed:
                        print(
                            f"tracking_maintenance expired={expired} removed={removed}"
                        )
                    next_maintenance = now + 1.0
                if now >= next_status:
                    counts = store.status_counts()
                    print(
                        f"tracking_status active={counts.get('active', 0)} "
                        f"ended={counts.get('ended', 0)} "
                        f"pending={runner.pending_count()} "
                        f"at={datetime.now(timezone.utc).isoformat()}"
                    )
                    next_status = now + args.status_seconds
                if batch.scanned < args.batch_size:
                    stop.wait(args.poll_ms / 1000)
        finally:
            print("tracking_engine_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
