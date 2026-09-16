#!/usr/bin/env python3
"""Report retained visual inputs available to a future embedding pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from visual_reid import iter_media_assets, summarize_assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--receiver-database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    args = parser.parse_args()
    assets = list(iter_media_assets(args.receiver_database))
    print(f"media_assets={len(assets)}")
    for row in summarize_assets(assets):
        dimensions = ",".join(row["dimensions"]) or "-"
        print(
            f"source={row['source']} camera={row['camera']} role={row['role']} "
            f"assets={row['assets']} present={row['present']} "
            f"valid_jpeg={row['valid_jpeg']} avg_bytes={row['average_bytes']} "
            f"dimensions={dimensions}"
        )
    missing = [asset for asset in assets if not asset.exists]
    if missing:
        print(f"missing_assets={len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
