#!/usr/bin/env python3
"""Start and supervise the repository's complete local processing stack."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
import threading
import time

from local_supervisor.configuration import (
    GPU_DETECTOR_ENDPOINT,
    build_gpu_detector_spec,
    build_specs,
    load_config,
)
from local_supervisor.console_log import TeeLog
from local_supervisor.docker_stack import DockerStack, check_docker, find_docker
from local_supervisor.infrastructure import Infrastructure, StartupError, Timeouts
from local_supervisor.power import keep_awake
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
    detector_spec = build_gpu_detector_spec(config, REPOSITORY_ROOT)
    if not specs:
        raise ValueError("local stack has no enabled services")

    checks: list[tuple[str, list[str]]] = []
    docker = find_docker()
    if config.docker_stack:
        checks.append(("docker", check_docker(docker, config.compose_file)))
    if detector_spec is not None:
        checks.append((detector_spec.name, check_spec(detector_spec)))
    checks.extend((spec.name, check_spec(spec)) for spec in specs)
    failed = False
    for name, problems in checks:
        status = "OK" if not problems else "ERROR"
        print(f"check service={name} status={status}", flush=True)
        for problem in problems:
            print(f"  missing_or_invalid={problem}", flush=True)
        failed = failed or bool(problems)
    if failed:
        return 2
    if args.check:
        print(
            f"stack_check=ok services={len(checks)} "
            f"docker_stack={str(config.docker_stack).lower()} "
            f"frigate_detector={config.frigate_detector}",
            flush=True,
        )
        return 0

    stop = threading.Event()

    def request_stop(_signal_number: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        # Ctrl+Break, the only console signal a detached process group receives.
        signal.signal(signal.SIGBREAK, request_stop)
    console_lock = threading.Lock()
    session = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    session_log_root = config.log_root / session
    console = sys.stdout
    sys.stdout = TeeLog(console, session_log_root / "supervisor.log")
    removed_files, removed_bytes = cleanup_logs(config.log_root)
    print(
        f"log_retention files={removed_files} bytes={removed_bytes} days=7",
        flush=True,
    )
    awake = keep_awake(True)
    print(f"power keep_awake={'on' if awake else 'unavailable'}", flush=True)

    def infrastructure_console(message: str) -> None:
        with console_lock:
            print(f"[infrastructure] {message}", flush=True)

    infrastructure = None
    if config.docker_stack:
        assert docker is not None
        detector = (
            ManagedService(detector_spec, REPOSITORY_ROOT, session_log_root, console_lock)
            if detector_spec is not None
            else None
        )
        infrastructure = Infrastructure(
            DockerStack(docker, config.compose_file),
            detector,
            GPU_DETECTOR_ENDPOINT,
            infrastructure_console,
            Timeouts(engine=config.docker_start_timeout_seconds),
        )
    services = [
        ManagedService(spec, REPOSITORY_ROOT, session_log_root, console_lock)
        for spec in specs
    ]
    exit_code = 0
    try:
        if infrastructure is not None:
            print(
                f"infrastructure_starting frigate_detector={config.frigate_detector}",
                flush=True,
            )
            infrastructure.start(stop)
        print(f"stack_starting services={len(services)}", flush=True)
        for service in services:
            service.start()
            if stop.wait(config.startup_delay_seconds):
                break
        next_status = 0.0
        next_retention = time.monotonic() + 3600
        while not stop.is_set():
            if infrastructure is not None:
                infrastructure.supervise()
            for service in services:
                service.maybe_restart()
            if time.monotonic() >= next_status:
                states = " ".join(
                    f"{service.spec.name}={service.state()}" for service in services
                )
                if infrastructure is not None:
                    states = f"{infrastructure.state()} {states}"
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
    except StartupError as error:
        if not stop.is_set():
            print(f"ERROR stack_startup_failed {error}", flush=True)
            exit_code = 3
    finally:
        print("stack_stopping", flush=True)
        for service in reversed(services):
            service.stop()
        if infrastructure is not None:
            infrastructure.stop()
        if awake:
            keep_awake(False)
        print("stack_stopped", flush=True)
        log = sys.stdout
        sys.stdout = console
        log.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
