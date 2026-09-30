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
- **TFLite CPU detector** until OpenVINO on CPU is measured against it (next
  roadmap step).

## Checks

```powershell
experiments\visual-reid\.venv\Scripts\python.exe -m unittest discover -s tests\deploy -p "test_*.py"
```

The tests reject IP addresses, credentials, LAN-facing ports, unpinned images,
placeholders missing from `env.example`, and changes to the clip retention.
They do not start Docker. On 2026-09-29 the template was also started in an
isolated Frigate 0.17.2 container: the configuration validated without
migration, and go2rtc received the substituted URLs.

## Current limits

- The development PC still runs its older Frigate and Mosquitto compose files
  (`experiments/frigate-adapter/docker-compose.mqtt.yml`). This stack uses the
  same container names and ports, so only one of them can run at a time.
- The Mosquitto listener is anonymous. That is acceptable only while it is
  published on loopback.
- `shm_size` and the 1 GB recording cache are Frigate's defaults for a few
  cameras; they are sized with the final camera inventory.
