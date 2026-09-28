#!/usr/bin/env python3
"""Download pinned Open Model Zoo weights and verify their provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "model-manifest.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    for model in manifest["models"]:
        for item in model["files"]:
            destination = args.output / item["path"]
            if _matches(destination, item):
                print(f"verified={destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            _download(item["url"], destination, item)
            print(f"downloaded={destination}")
    return 0


def _download(url: str, destination: Path, expected: dict[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                temporary.write(chunk)
        if not _matches(temporary_path, expected):
            raise RuntimeError(f"model checksum or size mismatch: {url}")
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _matches(path: Path, expected: dict[str, object]) -> bool:
    if not path.is_file() or path.stat().st_size != int(expected["bytes"]):
        return False
    digest = hashlib.sha384()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == expected["sha384"]


if __name__ == "__main__":
    raise SystemExit(main())
