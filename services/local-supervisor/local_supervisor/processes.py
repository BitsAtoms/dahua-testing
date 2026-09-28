"""Restarting child-process management with clean cross-platform shutdown."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import TextIO

from .configuration import ServiceSpec


class ManagedService:
    def __init__(
        self,
        spec: ServiceSpec,
        repository_root: Path,
        log_root: Path,
        console_lock: threading.Lock,
    ) -> None:
        self.spec = spec
        self.repository_root = repository_root
        self.log_path = log_root / f"{spec.name}.log"
        self.console_lock = console_lock
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None
        self.log_output: TextIO | None = None
        self.restart_delay = 1.0
        self.restart_at = 0.0
        self.started_at = 0.0
        self.stopping = False

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_output = self.log_path.open("a", encoding="utf-8", newline="\n")
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(
            self.spec.command,
            cwd=self.repository_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )
        self.started_at = time.monotonic()
        self.reader = threading.Thread(
            target=self._read_output,
            name=f"log-{self.spec.name}",
            daemon=True,
        )
        self.reader.start()
        self._console(f"started pid={self.process.pid}")

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def maybe_restart(self) -> bool:
        if self.stopping:
            return False
        if self.process is None:
            if time.monotonic() >= self.restart_at:
                self.start()
                return True
            return False
        return_code = self.process.poll()
        if return_code is None:
            if time.monotonic() - self.started_at >= 60:
                self.restart_delay = 1.0
            return False
        self._console(
            f"exited code={return_code}; restart_in={self.restart_delay:g}s"
        )
        self._close_process_handles()
        self.restart_at = time.monotonic() + self.restart_delay
        self.restart_delay = min(self.restart_delay * 2, 30.0)
        return False

    def stop(self, timeout: float = 20.0) -> None:
        self.stopping = True
        process = self.process
        if process is None or process.poll() is not None:
            self._close_process_handles()
            return
        self._console("stopping")
        try:
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._console(f"stopped code={process.returncode}")
        self._close_process_handles()

    def state(self) -> str:
        if self.process is None:
            return "waiting"
        return "running" if self.process.poll() is None else f"exited({self.process.returncode})"

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for raw_line in self.process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            timestamp = datetime.now(timezone.utc).isoformat()
            if self.log_output is not None:
                self.log_output.write(f"{timestamp} {line}\n")
                self.log_output.flush()
            self._console(line)

    def _console(self, message: str) -> None:
        with self.console_lock:
            print(f"[{self.spec.name}] {message}", flush=True)

    def _close_process_handles(self) -> None:
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(timeout=1)
        if self.process is not None and self.process.stdout is not None:
            self.process.stdout.close()
        if self.log_output is not None:
            self.log_output.close()
        self.process = None
        self.reader = None
        self.log_output = None


def check_spec(spec: ServiceSpec) -> list[str]:
    problems = [str(path) for path in spec.required_paths if not path.is_file()]
    executable = Path(spec.command[0])
    if not executable.is_file():
        problems.insert(0, str(executable))
        return problems
    if spec.required_imports:
        imports = ";".join(f"import {name}" for name in spec.required_imports)
        result = subprocess.run(
            [str(executable), "-c", imports],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            problems.append(
                f"python imports ({', '.join(spec.required_imports)}): "
                f"{detail[-1] if detail else 'failed'}"
            )
    for host, port in spec.listen_endpoints:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
        except OSError as error:
            problems.append(f"listen address {host}:{port}: {error}")
        finally:
            probe.close()
    return problems
