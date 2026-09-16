#!/usr/bin/env python3
"""Start and supervise the repository's complete local processing stack."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import threading
import time

from local_supervisor.configuration import build_specs, load_config
from local_supervisor.processes import ManagedService, check_spec
from local_supervisor.retention import cleanup_logs


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("services/local-supervisor/local-stack.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate commands and dependencies without starting services",
    )
    args = parser.parse_args()
    config_path = (
        args.config.resolve()
        if args.config.is_absolute()
        else (REPOSITORY_ROOT / args.config).resolve()
    )
    config = load_config(config_path, REPOSITORY_ROOT)
    specs = build_specs(config, REPOSITORY_ROOT)
    if not specs:
        raise ValueError("local stack has no enabled services")

    failed = False
    for spec in specs:
        problems = check_spec(spec)
        status = "OK" if not problems else "ERROR"
        print(f"check service={spec.name} status={status}", flush=True)
        for problem in problems:
            print(f"  missing_or_invalid={problem}", flush=True)
        failed = failed or bool(problems)
    if failed:
        return 2
    if args.check:
        print(f"stack_check=ok services={len(specs)}", flush=True)
        return 0

    stop = threading.Event()

    def request_stop(_signal_number: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    console_lock = threading.Lock()
    session = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    session_log_root = config.log_root / session
    removed_files, removed_bytes = cleanup_logs(config.log_root)
    print(
        f"log_retention files={removed_files} bytes={removed_bytes} days=7",
        flush=True,
    )
    services = [
        ManagedService(spec, REPOSITORY_ROOT, session_log_root, console_lock)
        for spec in specs
    ]
    try:
        print(f"stack_starting services={len(services)}", flush=True)
        for service in services:
            service.start()
            if stop.wait(config.startup_delay_seconds):
                break
        next_status = 0.0
        next_retention = time.monotonic() + 3600
        while not stop.is_set():
            for service in services:
                service.maybe_restart()
            if time.monotonic() >= next_status:
                states = " ".join(
                    f"{service.spec.name}={service.state()}" for service in services
                )
                print(f"stack_status {states}", flush=True)
                next_status = time.monotonic() + config.status_seconds
            if time.monotonic() >= next_retention:
                removed_files, removed_bytes = cleanup_logs(config.log_root)
                print(
                    f"log_retention files={removed_files} bytes={removed_bytes} days=7",
                    flush=True,
                )
                next_retention = time.monotonic() + 3600
            stop.wait(0.25)
    finally:
        print("stack_stopping", flush=True)
        for service in reversed(services):
            service.stop()
        print("stack_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
