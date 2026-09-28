#!/usr/bin/env python3
"""Ingest recorded track_update.v1 JSONL into the local receiver store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from track_receiver import ContractError, ReceiverStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("runtime/track-receiver/receiver.sqlite3"),
    )
    args = parser.parse_args()
    accepted = 0
    duplicates = 0
    invalid = 0

    with ReceiverStore(args.database.resolve()) as store:
        removed = store.cleanup()
        for path in args.inputs:
            with path.open("r", encoding="utf-8-sig") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        update = json.loads(line)
                        result = store.ingest(update)
                    except (json.JSONDecodeError, ContractError, ValueError) as error:
                        invalid += 1
                        print(
                            f"invalid file={path} line={line_number} error={error}",
                            flush=True,
                        )
                        continue
                    if result.inserted:
                        accepted += 1
                    else:
                        duplicates += 1
        print(
            f"ingest_complete accepted={accepted} duplicates={duplicates} "
            f"invalid={invalid} retention_removed={removed} total={store.count()}"
        )
        for item in store.summary():
            print(
                f"source={item['source_type']} camera={item['camera_id']} "
                f"phase={item['phase']} messages={item['messages']}"
            )
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
