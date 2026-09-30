#!/usr/bin/env python3
"""Replay the retained recordings through an isolated Frigate, once per detector.

For each profile and camera group the script writes a Frigate config (JSON is
valid YAML), recreates the bench container, discards a warm-up period and then
records `frigate/events` for a fixed window. Person presence is rebuilt from
the event lifecycle (the same messages the Frigate adapter consumes) and
reduced to per-scenario proxies: seconds with a person, maximum simultaneous
people and tracks per loop. There is no per-second ground truth.

  python experiments\\frigate-replay-bench\\bench.py --profiles openvino-mobilenet zmq-rfdetr-m-320
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
COMPOSE = ROOT / "compose.yml"
BENCH_ROOT = REPOSITORY / "runtime" / "frigate-bench"
MODELS = REPOSITORY / "experiments" / "frigate-zmq-detector" / "models"
OUTPUT = ROOT / "output"
LOOP_ARGS = "-re -stream_loop -1 -fflags +genpts"


# ---------------------------------------------------------------- config

def build_config(profile: dict, scenarios: list[dict]) -> dict:
    cameras = {}
    for scenario in scenarios:
        width, height = scenario["detect_size"]
        cameras[scenario["name"]] = {
            "ffmpeg": {"inputs": [{"path": f"/bench/{scenario['path']}", "input_args": LOOP_ARGS, "roles": ["detect"]}]},
            "detect": {"enabled": True, "width": width, "height": height, "fps": 5},
        }
    return {
        "version": "0.17-0",
        "mqtt": {"enabled": True, "host": "frigate-bench-mqtt", "port": 1883, "topic_prefix": "frigate"},
        "detectors": profile["detectors"],
        "model": profile["model"],
        "objects": {"track": ["person"]},
        "record": {"enabled": False},
        "snapshots": {"enabled": False},
        "birdseye": {"enabled": False},
        "face_recognition": {"enabled": False},
        "semantic_search": {"enabled": False},
        "lpr": {"enabled": False},
        "classification": {"bird": {"enabled": False}},
        "cameras": cameras,
    }


# --------------------------------------------------------------- metrics

@dataclass
class Track:
    camera: str
    start: float
    end: float | None = None
    top_score: float = 0.0


def person_tracks(messages: list[tuple[float, dict]]) -> dict[str, Track]:
    """Confirmed person tracks from (receipt_time, frigate/events payload)."""
    tracks: dict[str, Track] = {}
    for receipt, payload in messages:
        after = payload.get("after") or {}
        if after.get("label") != "person" or after.get("false_positive"):
            continue
        track_id = after["id"]
        moment = float(after.get("frame_time") or receipt)
        track = tracks.setdefault(track_id, Track(after["camera"], moment))
        track.start = min(track.start, moment)
        track.top_score = max(track.top_score, float(after.get("top_score") or after.get("score") or 0.0))
        if payload.get("type") == "end":
            track.end = float(after.get("end_time") or moment)
    return tracks


def per_second_counts(tracks: list[Track], start: float, end: float) -> list[int]:
    counts = []
    for second in range(int(end - start)):
        moment = start + second + 0.5
        counts.append(sum(1 for t in tracks if t.start <= moment < (t.end if t.end is not None else end)))
    return counts


def scenario_metrics(scenario: dict, tracks: list[Track], start: float, end: float) -> dict:
    counts = per_second_counts(tracks, start, end)
    window = end - start
    loops = window / scenario["duration_s"]
    in_window = [t for t in tracks if t.start < end and (t.end is None or t.end > start)]
    person_seconds = sum(1 for count in counts if count >= 1)
    return {
        "scenario": scenario["name"],
        "expected_person": scenario["expected_person"],
        "expected_tracks_per_loop": scenario["expected_tracks"],
        "window_s": round(window, 1),
        "loops": round(loops, 2),
        "person_seconds": person_seconds,
        "person_fraction": round(person_seconds / len(counts), 3) if counts else 0.0,
        "max_simultaneous": max(counts, default=0),
        "tracks": len(in_window),
        "tracks_per_loop": round(len(in_window) / loops, 2) if loops else 0.0,
        "median_top_score": round(statistics.median(t.top_score for t in in_window), 3) if in_window else None,
    }


# ---------------------------------------------------------------- docker

def compose(*args: str) -> None:
    environment = dict(os.environ, BENCH_ROOT=str(BENCH_ROOT))
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), *args], check=True, env=environment,
                   stdout=subprocess.DEVNULL)


def bench_stats() -> dict | None:
    completed = subprocess.run(["docker", "exec", "frigate-bench", "curl", "-sf", "http://127.0.0.1:5000/api/stats"],
                               capture_output=True, text=True, timeout=20)
    try:
        return json.loads(completed.stdout) if completed.returncode == 0 else None
    except json.JSONDecodeError:
        return None


def wait_until_ready(cameras: list[str], timeout_s: float = 240) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        stats = bench_stats()
        if stats and all(stats["cameras"].get(c, {}).get("camera_fps", 0) > 0 for c in cameras):
            return
        time.sleep(3)
    raise RuntimeError("bench Frigate did not deliver frames for every camera in time")


class EventRecorder:
    def __init__(self) -> None:
        self.messages: list[tuple[float, dict]] = []
        self.process = subprocess.Popen(
            ["docker", "exec", "frigate-bench-mqtt", "mosquitto_sub", "-t", "frigate/events"],
            stdout=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                self.messages.append((time.time(), json.loads(line)))
            except json.JSONDecodeError:
                pass

    def stop(self) -> None:
        self.process.terminate()
        self.thread.join(timeout=5)


def zmq_client_listening(port: int = 5555) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


@dataclass
class RunResult:
    profile: str
    group: str
    rows: list[dict] = field(default_factory=list)
    resources: dict = field(default_factory=dict)


def run(profile_name: str, profile: dict, group: str, scenarios: list[dict], warmup_s: float, window_s: float) -> RunResult:
    if profile["detectors"][next(iter(profile["detectors"]))]["type"] == "zmq" and not zmq_client_listening():
        raise RuntimeError("zmq profile needs experiments/frigate-zmq-detector/run_detector.ps1 running")
    config_dir = BENCH_ROOT / "config"
    (config_dir / "model_cache").mkdir(parents=True, exist_ok=True)
    (BENCH_ROOT / "media").mkdir(parents=True, exist_ok=True)
    if "model_file" in profile:
        shutil.copy2(MODELS / profile["model_file"], config_dir / "model_cache" / profile["model_file"])
    (config_dir / "config.yml").write_text(json.dumps(build_config(profile, scenarios), indent=2), encoding="utf-8")

    compose("up", "-d", "--force-recreate")
    names = [s["name"] for s in scenarios]
    recorder = EventRecorder()
    wait_until_ready(names)
    time.sleep(warmup_s)
    start = time.time()
    samples = []
    while time.time() < start + window_s:
        stats = bench_stats()
        if stats:
            samples.append(stats)
        time.sleep(10)
    end = time.time()
    recorder.stop()

    tracks = person_tracks(recorder.messages)
    result = RunResult(profile_name, group)
    for scenario in scenarios:
        camera_tracks = [t for t in tracks.values() if t.camera == scenario["name"]]
        result.rows.append(scenario_metrics(scenario, camera_tracks, start, end))
    inference = [d["inference_speed"] for s in samples for d in s["detectors"].values()]
    skipped = [s["cameras"][n]["skipped_fps"] for s in samples for n in names]
    camera_fps = [s["cameras"][n]["camera_fps"] for s in samples for n in names]
    result.resources = {
        "inference_ms_median": round(statistics.median(inference), 1) if inference else None,
        "skipped_fraction": round(sum(skipped) / sum(camera_fps), 3) if sum(camera_fps) else None,
        "event_messages": len(recorder.messages),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    profiles = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))["profiles"]
    scenarios = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    parser.add_argument("--profiles", nargs="+", default=list(profiles))
    parser.add_argument("--groups", nargs="+", default=sorted({s["group"] for s in scenarios}))
    parser.add_argument("--warmup", type=float, default=30.0)
    parser.add_argument("--loops-of-longest", type=float, default=2.0)
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUTPUT / stamp
    output.mkdir(parents=True, exist_ok=True)
    try:
        for group in args.groups:
            selected = [s for s in scenarios if s["group"] == group]
            window = math.ceil(max(s["duration_s"] for s in selected) * args.loops_of_longest)
            for name in args.profiles:
                print(f"== {name} / {group}: warm-up {args.warmup:.0f} s, window {window} s", flush=True)
                result = run(name, profiles[name], group, selected, args.warmup, window)
                with (output / "results.jsonl").open("a", encoding="utf-8") as sink:
                    sink.write(json.dumps({"profile": name, "group": group, "resources": result.resources, "rows": result.rows}) + "\n")
                print(json.dumps(result.resources), flush=True)
                for row in result.rows:
                    print("  " + json.dumps(row), flush=True)
    finally:
        compose("down")
    print(f"results: {output / 'results.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
