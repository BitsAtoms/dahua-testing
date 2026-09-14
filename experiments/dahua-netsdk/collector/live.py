#!/usr/bin/env python3
"""Run one live Dahua CGI + NetSDK hybrid collector."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request

from dahua_collector.adapters import CgiHumanTraitStreamParser, load_netsdk_event
from dahua_collector.correlation import DahuaEventCorrelator
from dahua_collector.sinks import EventSink, JsonlEventSink


_EVENT_FILE_RE = re.compile(r"^event_json=(?P<path>.+)$")
_LIVE_TRACK_RE = re.compile(r"^live_track_update=(?P<payload>\{.+\})$")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, original in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Malformed .env line {line_number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(source: str) -> dict[str, str]:
    if source == "-":
        return dict(os.environ)
    return read_env(Path(source).resolve())


def require(config: dict[str, str], key: str) -> str:
    value = config.get(key, "")
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def cgi_worker(
    config: dict[str, str],
    raw_log: Path,
    messages: queue.Queue[tuple[str, Any]],
    stop: threading.Event,
) -> None:
    host = require(config, "DAHUA_HOST")
    user = require(config, "DAHUA_USER")
    password = require(config, "DAHUA_PASSWORD")
    http_port = int(config.get("DAHUA_HTTP_PORT", "80"))
    url = (
        f"http://{host}:{http_port}/cgi-bin/eventManager.cgi"
        "?action=attach&codes=%5BAll%5D"
    )
    password_manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_manager.add_password(None, url, user, password)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPDigestAuthHandler(password_manager)
    )
    parser = CgiHumanTraitStreamParser()
    retry_seconds = 1.0
    read_timeout = float(config.get("DAHUA_CGI_READ_TIMEOUT", "300"))

    with raw_log.open("a", encoding="utf-8", newline="\n") as log:
        while not stop.is_set():
            try:
                with opener.open(url, timeout=read_timeout) as response:
                    messages.put(("status", "CGI connected"))
                    retry_seconds = 1.0
                    while not stop.is_set():
                        raw_line = response.readline()
                        if not raw_line:
                            raise ConnectionError("CGI stream closed by camera")
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        log.write(line + "\n")
                        log.flush()
                        for event in parser.feed_line(line):
                            event["_received_at"] = datetime.now(
                                timezone.utc
                            ).isoformat()
                            messages.put(("cgi", event))
            except Exception as error:  # transport errors are retried centrally
                if stop.is_set():
                    return
                messages.put(("warning", f"CGI disconnected: {error}"))
                stop.wait(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 30.0)


def load_event_file(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    last_error: Exception | None = None
    for _ in range(20):
        try:
            return load_netsdk_event(path)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(0.05)
    raise RuntimeError(f"Cannot read NetSDK event file {path}: {last_error}")


def netsdk_worker(
    executable: Path,
    config: dict[str, str],
    raw_output: Path,
    messages: queue.Queue[tuple[str, Any]],
    stop: threading.Event,
    process_holder: list[subprocess.Popen[str]],
) -> None:
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    child_environment = os.environ.copy()
    for key in ("DAHUA_HOST", "DAHUA_PORT", "DAHUA_USER", "DAHUA_PASSWORD"):
        child_environment[key] = require(config, key)
    process = subprocess.Popen(
        [str(executable), "-", str(raw_output)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
        env=child_environment,
    )
    process_holder.append(process)
    assert process.stdout is not None
    for original in process.stdout:
        line = original.rstrip("\r\n")
        track_match = _LIVE_TRACK_RE.match(line)
        if track_match:
            try:
                messages.put(("live-track", json.loads(track_match.group("payload"))))
            except json.JSONDecodeError as error:
                messages.put(("warning", f"Invalid live track payload: {error}"))
            continue
        if line:
            messages.put(("sdk-log", line))
        match = _EVENT_FILE_RE.match(line)
        if match:
            try:
                event = load_event_file(match.group("path"))
                event["_python_received_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
                messages.put(("netsdk", event))
            except Exception as error:
                messages.put(("error", str(error)))
        if stop.is_set():
            break
    return_code = process.wait()
    if not stop.is_set() and return_code != 0:
        messages.put(("error", f"NetSDK process exited with code {return_code}"))


def stop_netsdk(process_holder: list[subprocess.Popen[str]]) -> None:
    if not process_holder:
        return
    process = process_holder[0]
    if process.poll() is not None:
        return
    try:
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        process.wait(timeout=15)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def write_normalized(
    output: EventSink, events: list[dict[str, Any]]
) -> None:
    for event in events:
        event["timing"]["collector_published_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        output.publish(event)
        media = ",".join(item["role"] for item in event["media"])
        projection = {
            "message_id": event["message_id"],
            "observation_id": event["observation_id"],
            "camera_id": event["camera_id"],
            "phase": event["phase"],
            "ingested_at": event["ingested_at"],
            "local_track_id": event["subject"]["local_track_id"],
            "quality": event["quality"]["status"],
            "media": event["media"],
            "observed_at": event["observed_at"],
            "timing": event["timing"],
        }
        print(
            "collector_observation="
            + json.dumps(projection, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )
        print(
            "normalized_event "
            f"camera={event['camera_id']} "
            f"phase={event['phase']} "
            f"track={event['subject']['local_track_id']} "
            f"group={event['source_data']['group_id']} "
            f"status={event['quality']['status']} "
            f"media={media or 'none'}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--camera-id")
    parser.add_argument(
        "--sdk-exe",
        type=Path,
        default=Path("experiments/dahua-netsdk/build/dahua-events.exe"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/dahua-netsdk/output/live"),
    )
    parser.add_argument("--ttl-seconds", type=float, default=10.0)
    parser.add_argument(
        "--control-stdin",
        action="store_true",
        help="stop cleanly when the supervisor writes to or closes stdin",
    )
    args = parser.parse_args()

    config = load_config(args.env_file)
    host = require(config, "DAHUA_HOST")
    camera_id = args.camera_id or config.get("DAHUA_CAMERA_ID") or f"dahua_{host.replace('.', '_')}"
    if not args.sdk_exe.is_file():
        raise FileNotFoundError(f"NetSDK executable not found: {args.sdk_exe}")

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_root = (args.output_root / camera_id / session).resolve()
    sdk_output = session_root / "netsdk"
    sdk_output.mkdir(parents=True)
    cgi_log = session_root / "cgi-events.log"
    normalized_path = session_root / "normalized-events.jsonl"
    live_tracks_path = session_root / "live-track-updates.jsonl"

    messages: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop = threading.Event()
    shutdown = threading.Event()
    process_holder: list[subprocess.Popen[str]] = []
    correlator = DahuaEventCorrelator(ttl_seconds=args.ttl_seconds)
    threads = [
        threading.Thread(
            target=cgi_worker,
            args=(config, cgi_log, messages, stop),
            name="dahua-cgi",
            daemon=True,
        ),
        threading.Thread(
            target=netsdk_worker,
            args=(args.sdk_exe.resolve(), config, sdk_output, messages, stop, process_holder),
            name="dahua-netsdk",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    if args.control_stdin:
        def wait_for_supervisor() -> None:
            sys.stdin.readline()
            shutdown.set()

        threading.Thread(
            target=wait_for_supervisor,
            name="supervisor-control",
            daemon=True,
        ).start()

    print(f"collector_started camera={camera_id}")
    print(f"session_output={session_root}")
    print("Press Ctrl+C to stop.", flush=True)

    try:
        with (
            JsonlEventSink(normalized_path) as output,
            JsonlEventSink(live_tracks_path) as live_tracks,
        ):
            live_track_announced = False
            while not shutdown.is_set():
                try:
                    kind, payload = messages.get(timeout=0.5)
                except queue.Empty:
                    write_normalized(output, correlator.expire())
                    continue

                if kind == "cgi":
                    write_normalized(output, correlator.ingest_cgi(camera_id, payload))
                elif kind == "netsdk":
                    write_normalized(output, correlator.ingest_netsdk(camera_id, payload))
                elif kind == "live-track":
                    payload["camera_id"] = camera_id
                    live_tracks.publish(payload)
                    if not live_track_announced:
                        print("netsdk: Live track feed receiving updates", flush=True)
                        live_track_announced = True
                elif kind in {"status", "warning", "error"}:
                    print(f"{kind}: {payload}", flush=True)
                elif kind == "sdk-log" and (
                    payload.startswith("SDK version=")
                    or payload.startswith("Login succeeded")
                    or payload.startswith("Subscribed")
                    or payload.startswith("Live track")
                    or payload.startswith("Analyzer event")
                    or payload.startswith("Camera ")
                ):
                    print(f"netsdk: {payload}", flush=True)
    except KeyboardInterrupt:
        print("Stopping collector...", flush=True)
    finally:
        stop.set()
        stop_netsdk(process_holder)
        print("collector_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
