#!/usr/bin/env python3
"""Replay recorded CGI and NetSDK data through the hybrid correlator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dahua_collector.adapters import load_netsdk_recording, parse_cgi_recording
from dahua_collector.correlation import DahuaEventCorrelator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cgi_log", type=Path)
    parser.add_argument("netsdk_output", type=Path)
    parser.add_argument("--camera-id", default="dahua_test")
    args = parser.parse_args()

    correlator = DahuaEventCorrelator()
    emitted = []
    for event in parse_cgi_recording(args.cgi_log):
        emitted.extend(correlator.ingest_cgi(args.camera_id, event))
    for event in load_netsdk_recording(args.netsdk_output):
        emitted.extend(correlator.ingest_netsdk(args.camera_id, event))

    print(json.dumps(emitted, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
