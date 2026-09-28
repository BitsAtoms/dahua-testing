#!/usr/bin/env python3
"""Publish recorded normalized updates to the common MQTT topic with QoS 1."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from track_receiver import ContractError, validate_track_update
from track_receiver.configuration import merged_config, mqtt_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    transport = mqtt_config(merged_config(args.env_file))

    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "paho-mqtt is missing; install services/track-receiver/requirements.txt"
        ) from error

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"track-replay-{os.getpid()}",
    )
    if transport["username"]:
        client.username_pw_set(
            str(transport["username"]), str(transport["password"])
        )
    client.connect(str(transport["host"]), int(transport["port"]), keepalive=60)
    client.loop_start()
    published = 0
    try:
        with args.input.open("r", encoding="utf-8-sig") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    update = json.loads(line)
                    validate_track_update(update)
                except (json.JSONDecodeError, ContractError) as error:
                    raise ValueError(
                        f"invalid update at line {line_number}: {error}"
                    ) from error
                wire = json.dumps(
                    update, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                result = client.publish(str(transport["topic"]), wire, qos=1)
                result.wait_for_publish(timeout=10)
                if not result.is_published():
                    raise TimeoutError(f"publish timed out at line {line_number}")
                published += 1
    finally:
        client.disconnect()
        client.loop_stop()
    print(f"publish_complete messages={published} topic={transport['topic']} qos=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
