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

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TRACK_TRANSPORT_ROOT = REPOSITORY_ROOT / "services" / "track-transport"
sys.path.insert(0, str(TRACK_TRANSPORT_ROOT))

from dahua_collector.adapters import CgiHumanTraitStreamParser, load_netsdk_event
from dahua_collector.correlation import DahuaEventCorrelator
from dahua_collector.live_lane import DahuaLiveLane, target_from_probe_line
from dahua_collector.sinks import EventSink, JsonlEventSink
from dahua_collector.track_updates import observation_to_track_update
from track_transport import MqttOutboxPublisher, OutboxStore


_EVENT_FILE_RE = re.compile(r"^event_json=(?P<path>.+)$")
_LIVE_TRACK_RE = re.compile(r"^live_track_update=(?P<payload>\{.+\})$")
_IVS_STATUS_PREFIXES = (
    "camera_model=",
    "camera_firmware=",
    "Login succeeded",
    "Camera reconnected",
    "stream_stalled",
    "Stopped after",
    "Clean shutdown",
)
_LANE_EXPIRE_INTERVAL_SECONDS = 0.5


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
    if "DAHUA_LIVE_TRACK_PROBE" in config:
        child_environment["DAHUA_LIVE_TRACK_PROBE"] = config[
            "DAHUA_LIVE_TRACK_PROBE"
        ]
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


def ivs_worker(
    executable: Path,
    config: dict[str, str],
    messages: queue.Queue[tuple[str, Any]],
    stop: threading.Event,
    process_holder: list[subprocess.Popen[str]],
) -> None:
    """Keep the live IVS child running and forward its targets.

    The child exits with code 2 when the video stream stalls; any unexpected
    exit ends the lane's open tracks and restarts the child with backoff.
    """
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    child_environment = os.environ.copy()
    for key in ("DAHUA_HOST", "DAHUA_PORT", "DAHUA_USER", "DAHUA_PASSWORD"):
        child_environment[key] = require(config, key)
    retry_seconds = 1.0
    while not stop.is_set():
        started = time.monotonic()
        process = subprocess.Popen(
            [str(executable), "-", "--stdout"],
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
        process_holder[:] = [process]
        assert process.stdout is not None
        for original in process.stdout:
            line = original.rstrip("\r\n")
            try:
                parsed = target_from_probe_line(line)
            except (ValueError, KeyError) as error:
                messages.put(("warning", f"IVS lane: {error}"))
                continue
            if parsed is not None:
                messages.put(("ivs-target", parsed))
            elif line.startswith("Camera disconnected"):
                messages.put(("ivs-disconnected", line))
            elif line.startswith(_IVS_STATUS_PREFIXES):
                messages.put(("ivs-log", line))
            elif "failed" in line:
                messages.put(("warning", f"IVS lane: {line}"))
        return_code = process.wait()
        messages.put(("ivs-disconnected", f"IVS lane exited with code {return_code}"))
        if stop.is_set():
            return
        if time.monotonic() - started > 60:
            retry_seconds = 1.0
        messages.put(
            ("warning", f"IVS lane exited with code {return_code}; "
                        f"restarting in {retry_seconds:.0f}s")
        )
        stop.wait(retry_seconds)
        retry_seconds = min(retry_seconds * 2, 30.0)


def publish_live(
    track_output: EventSink,
    track_publisher: MqttOutboxPublisher,
    updates: list[dict[str, Any]],
) -> None:
    for update in updates:
        track_output.publish(update)
        track_publisher.publish(update)
        if update["phase"] in {"new", "end"}:
            reason = update["quality"].get("end_reason")
            print(
                f"ivs: live_track phase={update['phase']} "
                f"local_track_id={update['subject']['local_track_id']}"
                + (f" reason={reason}" if reason else ""),
                flush=True,
            )


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


def live_track_for(
    lane: DahuaLiveLane | None, event: dict[str, Any]
) -> tuple[str, float, float] | None:
    """Find the live-lane track that shares this HumanTrait's ObjectID."""
    local_track_id = event["subject"].get("local_track_id")
    if lane is None or not isinstance(local_track_id, str) or not local_track_id.isdigit():
        return None
    return lane.track_for_object(int(local_track_id))


def write_normalized(
    output: EventSink,
    track_output: EventSink,
    track_publisher: MqttOutboxPublisher,
    events: list[dict[str, Any]],
    lane: DahuaLiveLane | None = None,
) -> None:
    for event in events:
        event["timing"]["collector_published_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        output.publish(event)
        live_track = live_track_for(lane, event)
        track_update = observation_to_track_update(event, live_track)
        track_output.publish(track_update)
        track_publisher.publish(track_update)
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
            f"media={media or 'none'} "
            f"live_track={'joined' if live_track else 'none'}",
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
        "--live-lane",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "publish live positions from the camera's IVS stream and attach "
            "HumanTrait photos to them by ObjectID (default: on)"
        ),
    )
    parser.add_argument(
        "--ivs-exe",
        type=Path,
        default=Path("experiments/dahua-netsdk/build/dahua-ivs-probe.exe"),
    )
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
    if args.live_lane and not args.ivs_exe.is_file():
        raise FileNotFoundError(f"IVS executable not found: {args.ivs_exe}")

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_root = (args.output_root / camera_id / session).resolve()
    sdk_output = session_root / "netsdk"
    sdk_output.mkdir(parents=True)
    cgi_log = session_root / "cgi-events.log"
    normalized_path = session_root / "normalized-events.jsonl"
    track_updates_path = session_root / "track-updates.jsonl"
    live_tracks_path = session_root / "live-track-updates.jsonl"
    ivs_samples_path = session_root / "ivs-new-track-samples.jsonl"

    track_host = config.get("TRACK_MQTT_HOST") or require(
        config, "FRIGATE_MQTT_HOST"
    )
    track_port = int(
        config.get("TRACK_MQTT_PORT")
        or config.get("FRIGATE_MQTT_PORT", "1883")
    )
    track_topic = config.get("TRACK_MQTT_TOPIC", "tracking/track-updates")
    safe_camera_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", camera_id).strip("._")
    if not safe_camera_id:
        raise ValueError("camera_id cannot be used as an outbox name")
    outbox_root = Path(config.get("TRACK_OUTBOX_ROOT", "runtime/track-outbox"))
    outbox_path = outbox_root / f"dahua-{safe_camera_id}.sqlite3"
    track_publisher = MqttOutboxPublisher(
        OutboxStore(outbox_path),
        host=track_host,
        port=track_port,
        topic=track_topic,
        client_id=f"track-publisher-dahua-{safe_camera_id}",
        username=config.get("TRACK_MQTT_USER")
        or config.get("FRIGATE_MQTT_USER", ""),
        password=config.get("TRACK_MQTT_PASSWORD")
        or config.get("FRIGATE_MQTT_PASSWORD", ""),
    )

    messages: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop = threading.Event()
    shutdown = threading.Event()
    process_holder: list[subprocess.Popen[str]] = []
    ivs_process_holder: list[subprocess.Popen[str]] = []
    correlator = DahuaEventCorrelator(ttl_seconds=args.ttl_seconds)
    lane = DahuaLiveLane(camera_id, session) if args.live_lane else None
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
    if lane is not None:
        threads.append(
            threading.Thread(
                target=ivs_worker,
                args=(args.ivs_exe.resolve(), config, messages, stop, ivs_process_holder),
                name="dahua-ivs",
                daemon=True,
            )
        )
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
    print(f"track_outbox={outbox_path.resolve()}")
    track_publisher.start()
    print("Press Ctrl+C to stop.", flush=True)

    try:
        with (
            JsonlEventSink(normalized_path) as output,
            JsonlEventSink(track_updates_path) as track_output,
            JsonlEventSink(live_tracks_path) as live_tracks,
            JsonlEventSink(ivs_samples_path) as ivs_samples,
        ):
            live_track_announced = False
            last_lane_expire = 0.0
            while not shutdown.is_set():
                try:
                    kind, payload = messages.get(timeout=0.5)
                except queue.Empty:
                    write_normalized(
                        output,
                        track_output,
                        track_publisher,
                        correlator.expire(),
                        lane=lane,
                    )
                    kind, payload = None, None

                # Under continuous target traffic the queue is never empty, so
                # silent tracks are expired on a clock rather than on idle.
                now = time.time()
                if lane is not None and now - last_lane_expire >= _LANE_EXPIRE_INTERVAL_SECONDS:
                    publish_live(track_output, track_publisher, lane.expire(now))
                    last_lane_expire = now
                if kind is None:
                    continue

                if kind == "ivs-target" and lane is not None:
                    target, frame = payload
                    updates = lane.ingest(target)
                    for update in updates:
                        if update["phase"] == "new":
                            # One raw record per track keeps an audit sample
                            # without storing every 10 Hz frame.
                            ivs_samples.publish({"track_id": update["track_id"], "frame": frame})
                    publish_live(track_output, track_publisher, updates)
                elif kind == "ivs-disconnected" and lane is not None:
                    print(f"ivs: {payload}", flush=True)
                    publish_live(
                        track_output, track_publisher, lane.close("camera_disconnected")
                    )
                elif kind == "ivs-log":
                    print(f"ivs: {payload}", flush=True)
                elif kind == "cgi":
                    object_id = payload.get("object_id")
                    if lane is not None and isinstance(object_id, int):
                        # HumanTrait is published when the camera closes its
                        # own track: the authoritative end of the live track.
                        publish_live(
                            track_output, track_publisher, lane.finalize(object_id, time.time())
                        )
                    write_normalized(
                        output,
                        track_output,
                        track_publisher,
                        correlator.ingest_cgi(camera_id, payload),
                        lane=lane,
                    )
                elif kind == "netsdk":
                    write_normalized(
                        output,
                        track_output,
                        track_publisher,
                        correlator.ingest_netsdk(camera_id, payload),
                        lane=lane,
                    )
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
        if lane is not None:
            # End open live tracks before the transport stops; the sink appends.
            with JsonlEventSink(track_updates_path) as track_output:
                publish_live(track_output, track_publisher, lane.close("collector_stopped"))
            stop_netsdk(ivs_process_holder)
        stop_netsdk(process_holder)
        track_publisher.stop()
        print("collector_stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
