#!/usr/bin/env python3
"""Continuously enrich new handoff candidates with local visual evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import signal
import sqlite3
import threading
import time
from typing import Any

from visual_reid.evaluator import VisualEvaluator, candidate_fingerprint
from visual_reid.evidence_store import EvidenceStore


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--device", default="CPU")
    parser.add_argument(
        "--tracking-database",
        type=Path,
        default=Path("runtime/tracking-engine/tracking.sqlite3"),
    )
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("runtime/visual-reid/evidence.sqlite3"),
    )
    args = parser.parse_args()
    if args.batch_size <= 0 or args.poll_seconds <= 0 or args.since_hours <= 0:
        raise ValueError("batch-size, poll-seconds and since-hours must be positive")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    evaluator = VisualEvaluator(args.receiver_database, ROOT, args.device)
    with EvidenceStore(args.database) as store:
        print(
            f"visual_reid_started model={evaluator.model_version} "
            f"device={args.device} database={args.database}"
        )
        while not stop.is_set():
            cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
            candidates = _load_candidates(
                args.tracking_database,
                round(cutoff.timestamp() * 1_000_000),
            )
            current = store.current_fingerprints(evaluator.model_version)
            eligible = [
                item
                for item in candidates
                if current.get(item["candidate_id"])
                != candidate_fingerprint(item)
            ]
            pending = eligible[: args.batch_size]
            for candidate in pending:
                evidence = evaluator.evaluate(candidate)
                store.save(evidence)
                print(
                    f"visual_evidence candidate={candidate['candidate_id']} "
                    f"ranking={evidence['ranking_score']:.3f} "
                    f"coverage={evidence['visual_coverage']:.3f} "
                    f"modalities={','.join(evidence['available_modalities']) or '-'}"
                )
            removed = store.cleanup()
            print(
                f"visual_reid_status candidates={len(candidates)} "
                f"pending={len(eligible) - len(pending)} "
                f"stored={store.count()} cleanup={removed}"
            )
            if args.once:
                break
            stop.wait(args.poll_seconds)
        print("visual_reid_stopped")
    return 0


def _load_candidates(path: Path, cutoff_us: int) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"tracking database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT candidate_id, origin_track_id, destination_track_id,
                   score AS timing_score, gap_seconds, observed_at, observed_us
            FROM handoff_candidates
            WHERE observed_us >= ?
            ORDER BY observed_us DESC, score DESC
            """,
            (cutoff_us,),
        )
        return [dict(row) for row in rows]
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
