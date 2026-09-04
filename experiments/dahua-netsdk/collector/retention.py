#!/usr/bin/env python3
"""Report or apply collector output retention."""

from __future__ import annotations

import argparse
from pathlib import Path

from dahua_collector.retention import RetentionPolicy, apply_retention, plan_retention


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/dahua-netsdk/output"),
    )
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete planned files; omission is always a read-only report",
    )
    args = parser.parse_args()

    if args.days <= 0:
        parser.error("--days must be positive")

    root = args.output_root.resolve()
    policy = RetentionPolicy(max_age_days=args.days)
    candidates = plan_retention(root, policy)
    total_bytes = sum(item.size for item in candidates)
    for item in candidates:
        print(f"{item.reason}\t{item.size}\t{item.path}")
    print(f"candidates={len(candidates)} bytes={total_bytes} apply={args.apply}")
    if args.apply:
        print(f"removed_bytes={apply_retention(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
