from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_ROOT = REPOSITORY_ROOT / "services" / "local-supervisor"
sys.path.insert(0, str(SUPERVISOR_ROOT))

from local_supervisor.docker_stack import DockerCommandError, DockerStack  # noqa: E402
from local_supervisor.infrastructure import (  # noqa: E402
    MQTT_ENDPOINT,
    Infrastructure,
    StartupError,
    Timeouts,
)


DETECTOR = ("127.0.0.1", 5555)
FAST = Timeouts(engine=1, mqtt=1, detector=1, frigate=1, poll_interval=0.001, restart_retry=0)


class FakeProcess:
    def __init__(self, code: int | None) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float) -> int | None:
        return self.code


class FakeDocker:
    def __init__(self, calls: list[str], engine: bool = True, frigate: str | None = None) -> None:
        self.calls = calls
        self.engine = engine
        self.frigate_started = frigate
        self.restart_result = 0
        self.fail_on: str | None = None

    def engine_ready(self) -> bool:
        return self.engine

    def start_desktop(self, timeout_seconds: float) -> None:
        self.calls.append("start_desktop")
        self.engine = True

    def compose_up(self, *services: str) -> None:
        self.calls.append(f"compose_up:{','.join(services)}")
        if self.fail_on in services:
            raise DockerCommandError(f"docker compose up -d {self.fail_on} failed code=1")
        if "frigate" in services and self.frigate_started is None:
            self.frigate_started = "created-by-compose"

    def compose_stop(self, *services: str) -> None:
        self.calls.append(f"compose_stop:{','.join(services)}")

    def started_at(self, container: str) -> str | None:
        return self.frigate_started

    def restart(self, container: str) -> None:
        self.calls.append(f"restart:{container}")

    def begin_restart(self, container: str) -> FakeProcess:
        self.calls.append(f"begin_restart:{container}")
        return FakeProcess(self.restart_result)


class FakeDetector:
    def __init__(self, calls: list[str], ports: set[tuple[str, int]]) -> None:
        self.calls = calls
        self.ports = ports
        self.exits_on_start = False
        self.restart_next = False
        self.current = "waiting"

    def start(self) -> None:
        self.calls.append("detector_start")
        if self.exits_on_start:
            self.current = "exited(1)"
        else:
            self.current = "running"
            self.ports.add(DETECTOR)

    def stop(self, timeout: float = 20.0) -> None:
        self.calls.append("detector_stop")
        self.ports.discard(DETECTOR)

    def maybe_restart(self) -> bool:
        if self.restart_next:
            self.restart_next = False
            self.calls.append("detector_restart")
            return True
        return False

    def state(self) -> str:
        return self.current


def frigate_config(detector_type: str) -> dict:
    return {"detectors": {"main": {"type": detector_type}}}


class InfrastructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self.console: list[str] = []
        self.ports: set[tuple[str, int]] = set()
        self.frigate_detector_type = "zmq"

    def build(self, docker: FakeDocker, gpu: bool = True) -> Infrastructure:
        def compose_up(*services: str) -> None:
            FakeDocker.compose_up(docker, *services)
            if "mqtt" in services:
                self.ports.add(MQTT_ENDPOINT)

        docker.compose_up = compose_up  # type: ignore[method-assign]
        self.detector = FakeDetector(self.calls, self.ports) if gpu else None
        return Infrastructure(
            docker,
            self.detector,
            DETECTOR,
            self.console.append,
            FAST,
            tcp=lambda host, port: (host, port) in self.ports,
            fetch_json=lambda url: frigate_config(self.frigate_detector_type),
        )

    def test_starts_docker_then_mqtt_detector_and_frigate_in_order(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls, engine=False))

        infrastructure.start(threading.Event())

        self.assertEqual(
            self.calls,
            ["start_desktop", "compose_up:mqtt", "detector_start", "compose_up:frigate"],
        )
        self.assertIn("frigate action=started", self.console)

    def test_frigate_started_by_docker_before_the_detector_is_restarted(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls, frigate="boot-time"))

        infrastructure.start(threading.Event())

        self.assertEqual(self.calls[-2:], ["compose_up:frigate", "restart:frigate"])
        self.assertLess(self.calls.index("detector_start"), self.calls.index("restart:frigate"))

    def test_cpu_mode_has_no_detector_and_keeps_running_frigate(self) -> None:
        self.frigate_detector_type = "openvino"
        infrastructure = self.build(FakeDocker(self.calls, frigate="boot-time"), gpu=False)

        infrastructure.start(threading.Event())

        self.assertEqual(self.calls, ["compose_up:mqtt", "compose_up:frigate"])
        self.assertIn("frigate action=none reason=already_running", self.console)
        self.assertFalse(any(line.startswith("ERROR") for line in self.console))

    def test_detector_mismatch_is_reported_loudly(self) -> None:
        self.frigate_detector_type = "openvino"
        infrastructure = self.build(FakeDocker(self.calls))

        infrastructure.start(threading.Event())

        self.assertIn(
            "ERROR frigate_detector_mismatch supervisor=gpu frigate=openvino",
            self.console,
        )

    def test_detector_that_exits_during_startup_fails_before_frigate(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls))
        self.detector.exits_on_start = True

        with self.assertRaisesRegex(StartupError, "frigate_gpu_detector exited"):
            infrastructure.start(threading.Event())

        self.assertNotIn("compose_up:frigate", self.calls)

    def test_docker_command_failure_stops_startup(self) -> None:
        docker = FakeDocker(self.calls)
        docker.fail_on = "mqtt"
        infrastructure = self.build(docker)

        with self.assertRaisesRegex(StartupError, "compose up -d mqtt failed"):
            infrastructure.start(threading.Event())

        self.assertNotIn("detector_start", self.calls)

    def test_unreachable_mqtt_times_out(self) -> None:
        docker = FakeDocker(self.calls)
        infrastructure = self.build(docker)
        docker.compose_up = lambda *services: None  # type: ignore[method-assign]

        with self.assertRaisesRegex(StartupError, "mqtt not ready"):
            infrastructure.start(threading.Event())

    def test_detector_restart_restarts_frigate_once_it_listens(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls))
        infrastructure.start(threading.Event())
        self.calls.clear()
        self.detector.restart_next = True
        self.ports.discard(DETECTOR)

        infrastructure.supervise()
        self.assertEqual(self.calls, ["detector_restart"])
        self.assertEqual(infrastructure.state().split()[-1], "frigate=restarting")

        self.ports.add(DETECTOR)
        infrastructure.supervise()
        infrastructure.supervise()
        infrastructure.supervise()

        self.assertEqual(self.calls, ["detector_restart", "begin_restart:frigate"])
        self.assertIn("frigate restarted reason=detector_restarted", self.console)

    def test_failed_frigate_restart_is_retried(self) -> None:
        docker = FakeDocker(self.calls)
        infrastructure = self.build(docker)
        infrastructure.start(threading.Event())
        self.calls.clear()
        docker.restart_result = 1
        self.detector.restart_next = True

        infrastructure.supervise()
        docker.restart_result = 0
        infrastructure.supervise()
        infrastructure.supervise()

        self.assertEqual(self.calls.count("begin_restart:frigate"), 2)
        self.assertIn("frigate restarted reason=detector_restarted", self.console)
        self.assertTrue(any(line.startswith("ERROR frigate_restart_failed") for line in self.console))

    def test_stop_stops_frigate_and_detector_but_not_mqtt(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls))
        infrastructure.start(threading.Event())
        self.calls.clear()

        infrastructure.stop()

        self.assertEqual(self.calls, ["compose_stop:frigate", "detector_stop"])

    def test_stop_before_docker_was_reached_does_not_call_docker(self) -> None:
        infrastructure = self.build(FakeDocker(self.calls, engine=False))

        infrastructure.stop()

        self.assertEqual(self.calls, ["detector_stop"])

    def test_stop_request_interrupts_startup(self) -> None:
        stop = threading.Event()
        stop.set()
        infrastructure = self.build(FakeDocker(self.calls, engine=False))

        with self.assertRaisesRegex(StartupError, "stop requested"):
            infrastructure.start(stop)


class DockerStackTests(unittest.TestCase):
    def test_started_at_only_for_running_container(self) -> None:
        outputs = iter(["true 2026-09-30T15:54:47.1Z\n", "false 0001-01-01T00:00:00Z\n"])

        def runner(command: list[str], **_options: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(command, 0, next(outputs), "")

        stack = DockerStack("docker", Path("compose.yml"), runner)

        self.assertEqual(stack.started_at("frigate"), "2026-09-30T15:54:47.1Z")
        self.assertIsNone(stack.started_at("frigate"))

    def test_missing_container_has_no_start_time(self) -> None:
        def runner(command: list[str], **_options: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(command, 1, "", "No such object: frigate")

        self.assertIsNone(DockerStack("docker", Path("c.yml"), runner).started_at("frigate"))

    def test_failed_command_raises_with_last_error_line(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_options: object) -> subprocess.CompletedProcess:
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "pulling\nenv file .env not found")

        stack = DockerStack("docker", Path("deploy/docker/compose.yml"), runner)

        with self.assertRaisesRegex(
            DockerCommandError, r"docker compose up -d mqtt failed code=1: env file .env not found"
        ):
            stack.compose_up("mqtt")
        self.assertEqual(
            commands[0],
            ["docker", "compose", "-f", str(Path("deploy/docker/compose.yml")), "up", "-d", "mqtt"],
        )

    def test_engine_timeout_means_not_ready(self) -> None:
        def runner(command: list[str], **options: object) -> subprocess.CompletedProcess:
            raise subprocess.TimeoutExpired(command, options["timeout"])

        self.assertFalse(DockerStack("docker", Path("c.yml"), runner).engine_ready())


if __name__ == "__main__":
    unittest.main()
