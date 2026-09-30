#!/usr/bin/env python3
"""Download the pinned Frigate ZMQ detector client into the ignored vendor/.

The client is the ONNX Runtime server published by the Frigate organization
(github.com/frigate-nvr/apple-silicon-detector, MIT). It is used unmodified;
every file is checked against the size and SHA-256 recorded here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request

COMMIT = "9dde22bd94b99eaf448d798a3375e266f137b315"
BASE = f"https://raw.githubusercontent.com/frigate-nvr/apple-silicon-detector/{COMMIT}"
FILES = {
    "detector/zmq_onnx_client.py": (26853, "cc17caa1b6269baaf6a7064f1831a5e3c2f4a7e70d41edeeb0309566c562dbc4"),
    "detector/model_util.py": (7925, "5b47b6b7ad1b1071512335fffd03478c78c5a9c5f6beab612b957d55678cf0b0"),
    "LICENSE": (1077, "dea0b1e038915661df829c37b83c4a61eee626ea63e6016d9000aa77443708b8"),
}
VENDOR = Path(__file__).resolve().parent / "vendor"


def main() -> int:
    for relative, (size, digest) in FILES.items():
        with urllib.request.urlopen(f"{BASE}/{relative}", timeout=30) as response:
            data = response.read()
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"{relative}: size/hash mismatch for commit {COMMIT}")
        target = VENDOR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"{relative} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
