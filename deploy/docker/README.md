# Docker stack: Frigate and Mosquitto

This folder is the versioned, site-neutral definition of the Docker part of
the system:

```text
compose.yml                    Frigate 0.17.2 + Mosquitto 2.1.2, loopback only
frigate/config.template.yml    Frigate configuration without site data
mosquitto/mosquitto.conf       Broker shared by Frigate and repository services
env.example                    Variables to copy into the ignored .env
```

Frigate handles the non-Dahua cameras (roadmap phase 3). Dahua cameras use
the native Dahua collector and do not go through Frigate.

## What stays out of Git

- `deploy/docker/.env`: camera URLs with credentials and the site folders.
- The Frigate config folder (`STACK_FRIGATE_CONFIG_DIR`): `config.yml` with
  masks and zones, the database, trained classification models.
- The Frigate media folder (`STACK_FRIGATE_MEDIA_DIR`): snapshots and clips.

Both folders default to the ignored `runtime/frigate/` directory.

## First start on a new PC

From the repository root in PowerShell:

```powershell
Copy-Item deploy\docker\env.example deploy\docker\.env
notepad deploy\docker\.env
New-Item -ItemType Directory -Force runtime\frigate\config, runtime\frigate\media
if (-not (Test-Path runtime\frigate\config\config.yml)) {
  Copy-Item deploy\docker\frigate\config.template.yml runtime\frigate\config\config.yml
}
docker compose -f deploy\docker\compose.yml up -d
```

Copy the template only once. After that, `config.yml` belongs to the site and
is edited in the Frigate UI or by hand; later template changes are merged into
it deliberately, never by overwriting it.

From then on the local supervisor (`services/local-supervisor`) starts
Docker Desktop, Mosquitto and Frigate in order, and stops Frigate when it
stops. If Frigate uses the GPU detector outside Docker, set
`"frigate_detector": "gpu"` in the supervisor's local configuration.

The template's two example cameras show the two stream layouts. Rename them,
and their `{FRIGATE_...}` variables, to the site cameras. The Frigate camera
names are also used by the Frigate adapter (`FRIGATE_CAMERA_FRAME_SIZES` in the
repository `.env`) and by the space map, so choose them before collecting data.

## Secrets

Frigate substitutes environment variables whose name starts with `FRIGATE_`
in go2rtc sources, camera input paths, MQTT and ONVIF credentials. Every
camera URL is therefore a variable:

```yaml
go2rtc:
  streams:
    eufy_example:
      - "{FRIGATE_EUFY_EXAMPLE_URL}"
```

Compose passes `deploy/docker/.env` to the Frigate container. Substitution uses
Python string formatting, so URL-encode reserved characters (`@ : / % { }`) in
passwords. Compose also interpolates `$` in unquoted values; single-quote such
values.

Verified with Frigate 0.17.2 on 2026-09-29: saving a mask and a zone through
the Frigate API (`PUT /api/config/set`, used by the UI editors) kept the
`{FRIGATE_...}` placeholders and the comments in `config.yml`; the resolved
URLs were never written to the file.

## Design choices

- **Loopback only.** Every published port listens on `127.0.0.1`: 8971 (web UI
  with login), 5000 (API without login, used by the Frigate adapter), 8554
  (go2rtc relays for troubleshooting) and 1883 (MQTT). WebRTC (8555) is not
  published; the local UI uses MSE. The owner decided that the system is only
  used from the final PC's own screen.
- **One connection per camera stream.** Cameras read the go2rtc relays at
  `rtsp://127.0.0.1:8554/<stream>`, so detect and record share a connection.
- **Event clips only, seven days** (owner decision 2026-09-28), recorded from
  the main stream (owner decision 2026-09-29). Retention mode `all` keeps every
  segment during an event so clips have no gaps while a person stands still.
- **Pinned versions.** `stable` would move to a new Frigate release on the next
  pull. The template's `version: 0.17-0` must match the image; without it,
  Frigate treats the file as an old configuration and rewrites it.
- **Face recognition off** until the owner's privacy review allows face
  comparison with visitors.
- **OpenVINO on CPU** with the bundled SSDLite MobileNet v2. Measured on the
  development PC (Intel i9-12900F) on 2026-09-30, five minutes each at the same
  load (about 13 detections per second): 6.1 ms per inference versus 11.3 ms
  for Frigate's default TFLite detector, but about 1.0 CPU core versus 0.4.
  Frigate compiles the CPU model with OpenVINO's latency defaults and exposes
  no thread setting. It is kept because OpenVINO is Frigate's recommended CPU
  runtime for the larger ONNX models (YOLOv9, D-FINE), and it is the fallback
  for a GPU detector outside Docker. Repeat the measurement on the final AMD
  CPU.

## Checks

```powershell
experiments\visual-reid\.venv\Scripts\python.exe -m unittest discover -s tests\deploy -p "test_*.py"
```

The tests reject IP addresses, credentials, LAN-facing ports, unpinned images,
placeholders missing from `env.example`, and changes to the clip retention.
They do not start Docker. On 2026-09-29 the template was also started in an
isolated Frigate 0.17.2 container: the configuration validated without
migration, and go2rtc received the substituted URLs.

## Cameras that fail through the go2rtc relay

Some cameras send their H.264 parameter sets (SPS/PPS) only inside the video,
not in the RTSP session description. The go2rtc relay does not forward them,
and Frigate's ffmpeg then logs `non-existing PPS 0 referenced` and
`decode_slice_header error` in a loop until the watchdog restarts it. In
go2rtc's `/api/streams`, such a producer shows an H.264 receiver without
`profile` or `level`. The same stream decodes cleanly when ffmpeg reads the
camera directly.

For such a camera, read it directly and give it a single connection:

```yaml
cameras:
  view_only_camera:
    ffmpeg:
      inputs:
        - path: "{FRIGATE_VIEW_ONLY_CAMERA_URL}"
          roles:
            - detect
    detect:
      enabled: false   # view only; Frigate still decodes frames for the live view
      width: 1280
      height: 720
      fps: 10          # without go2rtc, the live view runs at this rate
    record:
      enabled: false
    snapshots:
      enabled: false
```

Frigate requires a `detect` input even when detection is disabled. Without a
go2rtc stream, the UI shows Frigate's own live view (jsmpeg). This happened
with one test camera on 2026-09-30.

## Development PC migration (2026-09-30)

The development PC runs this stack. `deploy/docker/.env` points both Frigate
folders at the existing `frigate-runtime` folder, so the database, the trained
`person_validity` model and past snapshots were kept. The Mosquitto
persistence was copied from the previous `frigate-adapter_frigate-mqtt-data`
volume into `batcomputer-mqtt-data`. After `up -d --force-recreate`, all three
cameras delivered frames, Frigate was online in MQTT, the classification model
loaded, and only loopback ports were listening.

Measured clip bitrate: the 3840x2160 H.264 main stream of the Hikvision
DeepinView records about 9 Mbps, that is about 4 GB per hour of events. With
10 occupied hours a day, that camera alone needs about 290 GB for the
seven-day retention.

## Current limits

- The Mosquitto listener is anonymous. That is acceptable only while it is
  published on loopback.
- `shm_size` and the 1 GB recording cache are Frigate's defaults for a few
  cameras; they are sized with the final camera inventory.
- go2rtc logs `can't add track ... audio, sendonly` for the Hikvision main
  stream: it is the camera's two-way audio channel and does not affect video
  or clips.
- go2rtc warnings print the full source URL, credentials included, in
  `docker logs frigate` and in the Frigate UI (Logs → go2rtc). Frigate's own
  log lines mask them. Never paste go2rtc logs anywhere, and mask
  `rtsp://user:password@` when filtering logs in scripts.
