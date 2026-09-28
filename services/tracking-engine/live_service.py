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
from tracking_engine import HandoffEngine, TrackingRunner, TrackingStore


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
    parser.add_argument(
        "--space-map",
        type=Path,
        default=Path("runtime/space-mapper/space-map.json"),
    )
    args = parser.parse_args()
    if args.poll_ms <= 0:
        raise ValueError("poll-ms must be positive")
    if args.status_seconds <= 0:
        raise ValueError("status-seconds must be positive")

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    with TrackingStore(args.database) as store:
        projection_reset = store.ensure_projection_version()
        handoffs = HandoffEngine(args.space_map, store)
        topology = None if projection_reset else handoffs.sync_topology()
        runner = TrackingRunner(
            args.receiver_database,
            store,
            validate_track_update,
            batch_size=args.batch_size,
            on_projected=None if projection_reset else handoffs.on_projected,
        )
        print(
            f"tracking_engine_started receiver={args.receiver_database} "
            f"database={args.database} poll_ms={args.poll_ms} "
            f"projection_reset={projection_reset}"
        )
        if topology is None:
            print(f"handoff_topology deferred=true map={args.space_map}")
        else:
            print(
                f"handoff_topology enabled={topology.enabled} "
                f"candidates={topology.candidates} map={args.space_map}"
            )
        rebuild_pending = projection_reset
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
                if rebuild_pending and batch.scanned < args.batch_size:
                    pending = runner.pending_count()
                    if pending == 0:
                        expired = store.expire_stale()
                        topology = handoffs.sync_topology(force=True)
                        runner.on_projected = handoffs.on_projected
                        rebuild_pending = False
                        print(
                            f"tracking_rebuild_complete tracks={store.count()} "
                            f"expired={expired} handoffs={topology.candidates}"
                        )
                if not rebuild_pending and now >= next_maintenance:
                    topology = handoffs.sync_topology()
                    if topology.changed:
                        print(
                            f"handoff_topology_reloaded enabled={topology.enabled} "
                            f"candidates={topology.candidates}"
                        )
                    expired = store.expire_stale()
                    if expired:
                        handoffs.rebuild()
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
                        f"handoffs={store.handoff_candidate_count()} "
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
