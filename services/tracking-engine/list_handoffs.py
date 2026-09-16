#!/usr/bin/env python3
"""Print recent spatial-temporal handoff candidates for diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("runtime/tracking-engine/tracking.sqlite3"),
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=0.0)
    args = parser.parse_args()
    if args.limit <= 0:
        raise ValueError("limit must be positive")
    if not 0 <= args.min_score <= 1:
        raise ValueError("min-score must be between 0 and 1")
    if not args.database.is_file():
        raise FileNotFoundError(f"tracking database does not exist: {args.database}")

    connection = sqlite3.connect(
        f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            """
            SELECT h.score, h.gap_seconds, h.observed_at,
                   h.origin_space_id, h.destination_space_id,
                   o.camera_id, h.origin_track_id,
                   d.camera_id, h.destination_track_id,
                   h.transition_id
            FROM handoff_candidates h
            JOIN local_tracks o ON o.track_id = h.origin_track_id
            JOIN local_tracks d ON d.track_id = h.destination_track_id
            WHERE h.score >= ?
            ORDER BY h.observed_us DESC, h.score DESC
            LIMIT ?
            """,
            (args.min_score, args.limit),
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        print("handoff_candidates=0")
        return 0
    print(f"handoff_candidates={len(rows)}")
    for row in rows:
        print(
            f"at={row[2]} score={row[0]:.3f} gap={row[1]:.3f}s "
            f"from={row[5]}:{row[6]}[{row[3]}] "
            f"to={row[7]}:{row[8]}[{row[4]}] transition={row[9]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
