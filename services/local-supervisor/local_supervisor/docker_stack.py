"""Thin wrapper over the Docker CLI: Docker Desktop and the Compose stack."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Callable


# Docker Desktop's default install location, for a logon task whose PATH was
# captured before Docker Desktop was installed.
DEFAULT_DOCKER = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")

Runner = Callable[..., subprocess.CompletedProcess]


class DockerCommandError(RuntimeError):
    pass


def find_docker() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    return str(DEFAULT_DOCKER) if DEFAULT_DOCKER.is_file() else None


def check_docker(docker: str | None, compose_file: Path) -> list[str]:
    problems = []
    if docker is None:
        problems.append("docker CLI (install Docker Desktop)")
    if not compose_file.is_file():
        problems.append(str(compose_file))
    # compose.yml requires it: camera URLs and site folders of Frigate.
    env_file = compose_file.parent / ".env"
    if not env_file.is_file():
        problems.append(str(env_file))
    return problems


class DockerStack:
    def __init__(
        self,
        docker: str,
        compose_file: Path,
        runner: Runner = subprocess.run,
    ) -> None:
        self.docker = docker
        self.compose_file = compose_file
        self.runner = runner

    def engine_ready(self) -> bool:
        try:
            result = self._run(["version", "--format", "{{.Server.Version}}"], 15)
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def start_desktop(self, timeout_seconds: float) -> None:
        # Waits until Docker Desktop reports that it is running.
        self._require(
            ["desktop", "start", "--timeout", str(int(timeout_seconds))],
            timeout_seconds + 30,
        )

    def compose_up(self, *services: str) -> None:
        self._require(self._compose("up", "-d", *services), 600)

    def compose_stop(self, *services: str) -> None:
        self._require(self._compose("stop", *services), 120)

    def started_at(self, container: str) -> str | None:
        """Start time of a running container; None if stopped or missing."""
        result = self._run(
            ["inspect", "--format", "{{.State.Running}} {{.State.StartedAt}}", container],
            30,
        )
        if result.returncode != 0:
            return None
        running, _, started = result.stdout.strip().partition(" ")
        return started if running == "true" and started else None

    def restart(self, container: str) -> None:
        self._require(["restart", container], 120)

    def begin_restart(self, container: str) -> subprocess.Popen:
        """Non-blocking restart, polled by the supervision loop."""
        return subprocess.Popen(
            [self.docker, "restart", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _compose(self, *arguments: str) -> list[str]:
        return ["compose", "-f", str(self.compose_file), *arguments]

    def _run(self, arguments: list[str], timeout: float) -> subprocess.CompletedProcess:
        return self.runner(
            [self.docker, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def _require(self, arguments: list[str], timeout: float) -> None:
        label = " ".join(_without_compose_file(arguments))
        try:
            result = self._run(arguments, timeout)
        except subprocess.TimeoutExpired as error:
            raise DockerCommandError(
                f"docker {label} timed out after {timeout:g}s"
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise DockerCommandError(
                f"docker {label} failed code={result.returncode}: "
                f"{detail[-1] if detail else 'no output'}"
            )


def _without_compose_file(arguments: list[str]) -> list[str]:
    if arguments[:2] == ["compose", "-f"]:
        return ["compose", *arguments[3:]]
    return arguments
