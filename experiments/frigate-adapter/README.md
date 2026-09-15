# External Frigate adapter proof of concept

This experiment consumes Frigate's public `frigate/events` MQTT payload rather
than modifying Frigate. It maps tracked objects to the shared
`../../contracts/track-update-v1.schema.json` contract.

Frigate emits `new`, `update`, and `end` when an accepted object starts,
changes materially, or leaves tracking. Its MQTT boxes use detect-stream pixel
coordinates, while the MQTT event does not carry that stream's dimensions.
The adapter therefore requires one `WIDTHxHEIGHT` value per camera and emits
geometry in the shared `normalized_0_1` coordinate space.

The Frigate `active` field means the inverse of `stationary`, not that the
track lifecycle is open. The adapter therefore uses `phase` for lifecycle and
publishes only the unambiguous `stationary` attribute.

Replay the sanitized fixture from the repository root:

```powershell
python experiments/frigate-adapter/replay.py `
  tests/frigate-events/fixtures/person-lifecycle.jsonl `
  --instance-id local-frigate `
  --frame-size front_door=1280x720
```

## Local MQTT transport

The included Mosquitto sidecar exposes its listener only on Windows localhost
and joins Frigate's existing Docker network:

```powershell
docker compose -f experiments/frigate-adapter/docker-compose.mqtt.yml up -d
```

This anonymous configuration is intentionally limited to the local proof of
concept. Production deployment requires authentication and TLS or a private
broker network.

Configure Frigate and restart it:

```yaml
mqtt:
  enabled: true
  host: frigate-mqtt
  port: 1883
  topic_prefix: frigate
```

Add these values to the ignored repository `.env` for the Windows adapter:

```dotenv
FRIGATE_MQTT_HOST=127.0.0.1
FRIGATE_MQTT_PORT=1883
FRIGATE_MQTT_TOPIC_PREFIX=frigate
FRIGATE_INSTANCE_ID=local-frigate
FRIGATE_CAMERA_FRAME_SIZES=puerta_planeta=1280x720,dahua_213=704x576
FRIGATE_API_URL=http://127.0.0.1:5000
```

Install the small client dependency and start the runner:

```powershell
python -m pip install -r experiments/frigate-adapter/requirements.txt
python experiments/frigate-adapter/mqtt_runner.py
```

Each run creates ignored `raw-events.jsonl`, `track-updates.jsonl`, and
`pipeline-timings.jsonl` files. The timing stream separates:

```text
EVENT AGE       Frigate frame time -> local MQTT callback
QUEUE           MQTT callback -> worker dequeue
JSON DECODE     payload decoding
RAW PERSIST     raw-event JSONL write and flush
NORMALIZE       Frigate payload -> track_update.v1
UPDATE PERSIST  normalized JSONL write and flush
MQTT>OUTPUT     local MQTT callback -> persisted track update
```

`EVENT AGE` includes Frigate's own event processing and broker delivery, so it
must not be interpreted as pure network latency. `MQTT>OUTPUT` is the local
adapter latency relevant to detecting queue, CPU, or disk bottlenecks. Internal
durations use a monotonic clock and remain valid if the system clock adjusts.

The MQTT callback uses a bounded queue and stops loudly instead of silently
dropping an event if the queue fills. All adapter output uses the same
seven-day retention policy as the Dahua collector.

Snapshot retrieval runs in a bounded worker pool outside the MQTT event path.
An `end` update is persisted immediately; when Frigate's JPEG becomes
available, the adapter emits a second `phase=snapshot` update with the same
`track_id` and an absolute media path. Bind Frigate's unauthenticated internal
API to Windows loopback only, never to the LAN:

```yaml
ports:
  - "127.0.0.1:5000:5000"
```

Frigate snapshots must be enabled separately and use the shared retention
policy:

```yaml
snapshots:
  enabled: true
  retain:
    default: 7
```

### Live validation

Validated against Frigate 0.17.2 on 2026-09-15. One person track produced the
complete `new -> update -> end -> snapshot` sequence. The `end` update was
persisted 0.394 ms after the local MQTT callback, and the asynchronous snapshot
update followed 37.3 ms later with the same `track_id`. The saved media was a
valid 704x576 JPEG inside the retained session directory. Real event IDs and
images remain in ignored output only.

## Future GPU deployment

The current proof of concept deliberately keeps Frigate on CPU. The production
computer is expected to have two GPUs, so hardware acceleration must be treated
as a deployment task before increasing the camera count:

- expose the selected GPU device IDs explicitly to the Frigate container;
- use the Frigate image matching the installed GPU runtime (for example,
  `stable-tensorrt` for supported NVIDIA hardware);
- move video decoding to the GPU with the appropriate FFmpeg preset;
- replace the default CPU detector with a supported GPU detector and model;
- assign decoding, object detection, and later visual embeddings to explicit
  GPUs instead of relying on automatic selection.

The initial allocation to benchmark is GPU 0 for Frigate decoding and object
detection, and GPU 1 for tracking/re-identification embeddings. This is a
starting hypothesis, not a fixed requirement: the final allocation must be
chosen from five-camera measurements of GPU utilization, VRAM, detector queue,
inference time, dropped frames, and end-to-end p50/p95/p99 latency. Preserve a
documented CPU fallback and verify restart behavior if either GPU is missing.
