#!/usr/bin/env python3
"""Supervise one isolated live collector process per Dahua camera."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from dahua_collector.configuration import CameraConfig, load_camera_config
from dahua_collector.retention import (
    RETENTION_MARKER,
    RetentionPolicy,
    apply_retention,
    plan_retention,
)
from live import read_env


class CameraWorker:
    def __init__(
        self,
        camera: CameraConfig,
        secrets: dict[str, str],
        live_script: Path,
        output_root: Path,
        messages: queue.Queue[tuple[str, str]],
    ) -> None:
        self.camera = camera
        self._environment = camera.worker_environment(secrets)
        self._live_script = live_script
        self._output_root = output_root
        self._messages = messages
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._restart_delay = 1.0
        self._restart_at = 0.0
        self._started_at = 0.0

    def start(self) -> None:
        environment = os.environ.copy()
        environment.update(self._environment)
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._process = subprocess.Popen(
            [
                sys.executable,
                str(self._live_script),
                "--env-file",
                "-",
                "--camera-id",
                self.camera.camera_id,
                "--output-root",
                str(self._output_root),
                "--control-stdin",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
            env=environment,
        )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._started_at = time.monotonic()
        self._messages.put((self.camera.camera_id, "worker_started"))

    def _read_output(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            line = line.rstrip("\r\n")
            if line:
                self._messages.put((self.camera.camera_id, line))

    def poll_and_restart(self) -> None:
        if self._stopping:
            return
        if self._process is None:
            if time.monotonic() >= self._restart_at:
                self.start()
            return
        return_code = self._process.poll()
        if return_code is None:
            if time.monotonic() - self._started_at >= 60.0:
                self._restart_delay = 1.0
            return
        self._messages.put(
            (self.camera.camera_id, f"worker_exited code={return_code}; restarting")
        )
        self._process = None
        self._restart_at = time.monotonic() + self._restart_delay
        self._restart_delay = min(self._restart_delay * 2, 30.0)

    def stop(self) -> None:
        self._stopping = True
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            assert process.stdin is not None
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=20)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/dahua-netsdk/output"),
    )
    parser.add_argument("--retention-days", type=float, default=7)
    parser.add_argument("--retention-interval-seconds", type=float, default=3600)
    args = parser.parse_args()

    if args.retention_days <= 0 or args.retention_interval_seconds <= 0:
        parser.error("retention values must be positive")

    retention_root = args.output_root.resolve()
    safe_default_root = Path(__file__).resolve().parents[1] / "output"
    marker = retention_root / RETENTION_MARKER
    if retention_root == safe_default_root:
        retention_root.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
    elif not marker.is_file():
        raise ValueError(
            f"custom output root must contain safety marker {RETENTION_MARKER}: "
            f"{retention_root}"
        )

    cameras = [camera for camera in load_camera_config(args.config) if camera.enabled]
    if not cameras:
        raise ValueError("configuration has no enabled cameras")
    secrets = read_env(args.env_file.resolve())
    messages: queue.Queue[tuple[str, str]] = queue.Queue()
    live_script = Path(__file__).with_name("live.py").resolve()
    workers = [
        CameraWorker(camera, secrets, live_script, args.output_root.resolve(), messages)
        for camera in cameras
    ]
    for worker in workers:
        worker.start()

    retention_policy = RetentionPolicy(max_age_days=args.retention_days)
    next_retention = 0.0
    print(
        f"supervisor_started cameras={len(workers)} "
        f"retention_days={args.retention_days:g}",
        flush=True,
    )
    try:
        while True:
            now = time.monotonic()
            if now >= next_retention:
                candidates = plan_retention(retention_root, retention_policy)
                removed_bytes = apply_retention(candidates)
                print(
                    f"retention_cleanup files={len(candidates)} "
                    f"bytes={removed_bytes}",
                    flush=True,
                )
                next_retention = now + args.retention_interval_seconds
            for worker in workers:
                worker.poll_and_restart()
            try:
                camera_id, message = messages.get(timeout=0.5)
                print(f"[{camera_id}] {message}", flush=True)
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        print("Stopping supervisor...", flush=True)
    finally:
        for worker in workers:
            worker.stop()
        print("supervisor_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
