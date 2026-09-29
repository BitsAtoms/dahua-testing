# Dahua NetSDK event experiment

Isolated Win64 proof of concept for receiving Dahua `HumanTrait` events and
extracting the JPEG images carried in the NetSDK callback buffer.

The experiment uses the repository's official `NetSDK` package without
modifying it. Credentials are read from the ignored root `.env` file.

## Required configuration

```dotenv
DAHUA_HOST=...
DAHUA_PORT=37777
DAHUA_USER=...
DAHUA_PASSWORD=...
# Diagnostic only; omit during normal operation:
# DAHUA_LIVE_TRACK_PROBE=1
```

Confirm the actual TCP/NetSDK port in the camera configuration. Do not use the
RTSP port (`554`) for SDK login.

## Configure and build

Open an x64 Visual Studio Developer Command Prompt in the repository root:

```cmd
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64
```

Then run:

```cmd
"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" -S experiments\dahua-netsdk -B experiments\dahua-netsdk\build -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release
"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" --build experiments\dahua-netsdk\build
```

The build copies the official runtime DLLs next to the executable.

## Controlled run

From the repository root:

```cmd
experiments\dahua-netsdk\build\dahua-events.exe .env experiments\dahua-netsdk\output
```

1. Begin with nobody in view.
2. Enter the scene naturally and remain visible for several seconds.
3. Face the camera briefly so it can produce a face crop.
4. Leave the scene completely.
5. Press Enter to unsubscribe, log out, and release the SDK.

The output directory is ignored by Git. Each callback writes an event JSON and
any valid JPEG slices described by the SDK event structure. On the tested
firmware, NetSDK reports `nObjectID=0`, so `local_track_id` remains unavailable
until the event is enriched with the CGI `ObjectID` by matching `GroupID`.
That `ObjectID` is a temporary camera-local track, not a persistent identity.

## Expected evidence

```text
Login succeeded
Subscribed to intelligent events with pictures
code=HumanTrait
local_track_id=...
buffer_size=...
saved_jpegs=...
Clean shutdown completed
```

The first validation target is body and face imagery. Panoramic/context images
are also inspected through `stuSceneImage`, `stuFaceSceneImage`, and the newer
typed image descriptor arrays when the camera provides them.

## CGI correlation test

The NetSDK structure used by this camera can leave `nObjectID` and `nEventID`
at zero. To determine the local track identifier without guessing, capture the
known CGI metadata stream at the same time:

```powershell
powershell.exe -ExecutionPolicy Bypass -File tests\dahua-events\capture-cgi.ps1
```

Run `dahua-events.exe` in a second terminal, perform one controlled entry and
exit, and stop both captures. Compare the results with:

```powershell
python tests\dahua-events\compare-events.py `
  tests\dahua-events\output\cgi-events.log `
  experiments\dahua-netsdk\output
```

`DAHUA_PORT` is the NetSDK TCP port. The CGI helper uses HTTP port 80 by
default; set `DAHUA_HTTP_PORT` separately in `.env` only when the camera web
service uses another port.

The controlled test confirmed exact body-event matches by `GroupID`. A facial
capture showed that CGI may emit the associated face as a separate event with
a different `GroupID`; link that event through the body's `RelativeID` and the
face's `ObjectID`/`BelongID`/`RelativeID`. Do not use the two `EventUUIDStr`
values as an equality key: the CGI and NetSDK values were close in time but
different. Do not rename `GroupID` to `local_track_id`; use the matched CGI
body `ObjectID` for that field.

## Hybrid correlator

The first transport-independent correlator is under:

```text
collector/dahua_collector/correlation.py
```

It keeps pending CGI and NetSDK records separated by `(camera_id, GroupID)`,
then links an optional CGI face event through the reciprocal object IDs. A
bounded TTL emits partial records instead of retaining unmatched input forever.
Raw source records are preserved in the normalized event.

Run its dependency-free tests from the repository root:

```powershell
python -m unittest discover -s tests\dahua-events -p "test_*.py" -v
```

Recorded CGI and NetSDK sessions can be replayed through the same correlator:

```powershell
python experiments\dahua-netsdk\collector\replay.py `
  tests\dahua-events\output\cgi-face-correlation-3.log `
  experiments\dahua-netsdk\output\face-correlation-run-3 `
  --camera-id dahua_213
```

The replay and live entry points feed the same transport-independent
correlation module, keeping the matching rules testable without a camera.

## Live hybrid collector

After building the native executable, run both transports and the correlator
as one foreground process:

```powershell
python -m pip install -r services\track-transport\requirements.txt
python experiments\dahua-netsdk\collector\live.py --camera-id dahua_213
```

Stop it with `Ctrl+C`. Each UTC-dated session under
`output/live/<camera-id>/` contains:

```text
cgi-events.log          raw CGI multipart stream
netsdk/                 raw NetSDK JSON and JPEG files
normalized-events.jsonl one correlated event per line
```

The live process retries the CGI connection with bounded backoff, relies on
NetSDK automatic reconnect for the native subscription, preserves raw source
records, and writes absolute snapshot paths into normalized events. It is a
foreground proof of concept, not yet an installed Windows service.

## Multi-camera supervisor

Copy `cameras.example.json` to the ignored `cameras.local.json` and add one
entry per camera. Hosts and ports live in that file; credentials are references
to keys in the ignored `.env`, not literal passwords.

Run all enabled cameras with:

```powershell
python experiments\dahua-netsdk\collector\supervisor.py `
  --config experiments\dahua-netsdk\cameras.local.json
```

The supervisor starts one isolated live worker per camera, prefixes its status
output with `camera_id`, restarts failed workers with bounded exponential
backoff, and requests a clean shutdown from every worker on `Ctrl+C`. The
worker passes credentials to the native SDK child through its process
environment rather than command-line arguments or generated config files.

## Event contracts and output destinations

The collector writes two complementary versioned contracts:

```text
../../contracts/observation-v1.schema.json
../../contracts/track-update-v1.schema.json
```

The CGI body is emitted immediately as an `observation`; NetSDK media and face
metadata are later `update` messages with the same `observation_id`. The UI no
longer waits for full correlation. Every message has a unique `message_id` for
idempotency, while source-specific structures remain under `raw`.

Each observation revision is also projected into the lean `track_update.v1`
contract and written to `track-updates.jsonl`. Its stable `track_id` is the
observation ID and `sequence` preserves the observation revision. Dahua
`HumanTrait` uses phase `snapshot` and quality `source_lifecycle=finalized_only`
because this firmware delivers the capture at track finalization; it must not
be presented to consumers as live position data. The contract also supports
`new`, `update` and `end` for a live source such as Frigate.

Provider-specific raw payloads remain exclusively in `observation.v1`. This
keeps the tracking connector small while retaining complete diagnostic data in
the collector output. Dahua's numeric AI attribute enums are not copied into
the tracking message until a provider-neutral semantic mapping is defined.

After the durable JSONL write, every `track_update.v1` is placed in a
per-camera SQLite outbox under `runtime/track-outbox/` and published to the
common `tracking/track-updates` topic with MQTT QoS 1. The shared transport
removes an outbox row only after the broker PUBACK and retries broker outages.
The receiver deduplicates possible redeliveries by `message_id`.

The worker uses `TRACK_MQTT_*` values from the ignored `.env` and falls back to
the local `FRIGATE_MQTT_HOST` and port. Keeping one outbox per camera preserves
process isolation and avoids SQLite write contention when several Dahua
cameras are running.

## Output retention

The agreed policy is a single seven-day lifetime for every collected file:
JPEG, CGI logs, NetSDK JSON, normalized JSONL, and any future file written
inside the collector output root. The multicamera supervisor applies it at
startup and once per hour while running.

The standalone command remains read-only by default and reports what would be
removed:

```powershell
python experiments\dahua-netsdk\collector\retention.py
```

The active supervisor policy is:

```text
all collected files: 7 days
cleanup interval:     1 hour
```

The standalone tool deletes only when the operator intentionally adds
`--apply`. The supervisor is the automatic retention owner and cleans its full
output root. There is no additional size-based deletion: files younger than
seven days are retained even if event volume is high.

## Local control interface

The local dashboard combines camera configuration, worker supervision, live
event status, latency, snapshot previews, and seven-day retention:

```powershell
python experiments/dahua-netsdk/collector/dashboard.py
```

Open `http://127.0.0.1:8090`. The service binds only to localhost. Camera
credentials entered in the form are saved as dedicated variables in the
ignored root `.env`; API responses never return them. `cameras.local.json`
remains the ignored non-secret camera inventory.

The compact monitor reports the worker, NetSDK, and CGI channels separately.
Its in-memory console keeps only the latest 200 important state, error, and
detection messages and can be cleared from the browser. Browser SSE disconnects
are handled as normal client lifecycle events, and Windows exclusive binding
prevents two dashboard instances from sharing the same port.

Latency instrumentation preserves timestamps for the camera's CGI `RealUTC`,
CGI receipt, the native NetSDK callback, Python receipt, correlation,
normalization, dashboard receipt, and browser SSE receipt. The card reports:

```text
EVENT AGE  camera RealUTC to browser receipt (not transport latency)
PIPE>UI    normalized observation to browser receipt
```

Hovering the latency row exposes CGI-to-dashboard, NetSDK callback-to-Python,
and correlation timings. `EVENT AGE` includes the lifetime of the camera-local
track: on the tested firmware, `RealUTC` identifies an early/best frame while
`HumanTrait` is commonly published when the track is finalized. It must not be
read as network latency. A high `PIPE>UI` indicates a collector, dashboard, or
browser delivery problem.

The dashboard receives only a small event projection. Raw structures stay in
JSONL and JPEG data is loaded through a local media URL, keeping the real-time
control channel independent from image size.

### Live-track capability probe

The optional `DAHUA_LIVE_TRACK_PROBE=1` diagnostic makes the native process
request the official `CLIENT_AttachVideoAnalyseTrackProc` feed in parallel
with `CLIENT_RealLoadPictureEx`. It is disabled by default because the tested
IPC accepted the subscription but emitted no track callbacks. If another
firmware or model supports it, bounded position samples are stored in the
session's `live-track-updates.jsonl`; the dashboard reports the first received
update without streaming the high-frequency payload through its operator
console. Failure to attach is non-fatal and `HumanTrait` capture continues
normally. Because the existing picture subscription uses
`EVENT_IVS_ALL`, the first three occurrences of any non-`HumanTrait` analyzer
event code are also reported for capability discovery.

## Live IVS stream probe

`HumanTrait` arrives when the camera-local track ends, so it cannot drive a
live map. The Web 5.0 live view nevertheless draws moving target boxes, which
Dahua carries as private IVS frames inside the real-time stream. The separate,
read-only `dahua-ivs-probe.exe` tests whether that data is reachable:

```text
NetSDK CLIENT_RealPlayEx (raw private stream, no window)
 -> PlaySDK PLAY_InputData / PLAY_Play (no window)
 -> PLAY_SetIVSCallBack
 -> ivs-frames.jsonl + summary.json
```

It is built with the collector by the commands above. PlaySDK (`play.dll`)
ships without an import library in this SDK package, so the probe loads it at
runtime and fails loudly if a required export is missing. It never changes
camera configuration and does not replace the collector.

At login it records the model and firmware reported by the camera itself
(`DH_DEVSTATE_SOFTWARE`), so each capability result is tied to the real
device. The serial number is not recorded.

Run from the repository root:

```cmd
experiments\dahua-netsdk\build\dahua-ivs-probe.exe .env experiments\dahua-netsdk\output --stream main
```

Options:

```text
--stream main|sub   real-time stream to open (default main)
--seconds N         stop automatically after N seconds (default: press Enter)
```

Controlled protocol, one person only, about 100 seconds
(`--seconds 100` stops automatically):

1. 0–20 s: nobody in view (baseline of rule/OSD frames).
2. 20–80 s: enter, cross the view slowly, stop about 5 s facing the camera,
   then leave the view completely.
3. 80–100 s: nobody in view again.

Each run creates `output/ivs-probe/<UTC>_<stream>/`:

```text
ivs-frames.jsonl  one line per IVS callback: arrival time, IVS type, length,
                  frame sequence and payload (text for JSON, hex otherwise)
summary.json      camera model/firmware, SDK versions, video bytes and
                  per-type counts, sizes and first/last arrival times
```

The shipped PlaySDK header documents the IVS types and states that
`IVSINFOTYPE_TRACK` carries `SP_IVS_OBJ_EX` objects and `IVSINFOTYPE_TRACK_EX_B0`
carries `SP_IVS_COMMON_OBJ` objects, but it does not define those structures.
Binary payloads are therefore preserved raw and must be decoded against the
controlled walk (for example, a box that moves with the person), not guessed.

Interpretation:

- IVS frames of a track type or JSON whose count and content change while the
  person is present: a live position lane is available; decode it next.
- Only rule, OSD or motion frames: the stream carries private data but no
  targets; check the camera's overlay settings with the owner before changing
  anything.
- No IVS frames at all: fall back to ONVIF metadata or regional counting as
  described in the root `ROADMAP.md`.

Summarize a session, optionally against a CGI attach capture recorded at the
same time with `tests\dahua-events\capture-cgi.ps1`:

```powershell
python experiments\dahua-netsdk\collector\analyze_ivs_probe.py `
  experiments\dahua-netsdk\output\ivs-probe\<session> `
  --cgi-log tests\dahua-events\output\<capture>.log
```

It prints presence intervals, each camera-local track ID with its time span
and horizontal movement, and whether that ID produced a CGI `HumanTrait`
event. The decoding it relies on (`collector/dahua_collector/ivs.py`) is the
empirical layout confirmed on `dahua_213` on 2026-09-29; results for that
model are in `docs/dahua-research.md`.

Add `--lifecycle` to replay the recorded targets through the collector's
live lane (`collector/dahua_collector/live_lane.py`) and print the
`new`/`end` messages it would publish.

Main-stream decoding measured about 7 % of one core on the development PC.
The sub-stream only replayed stale IVS frames at connection, so the live
lane uses the main stream.

## Live lane (in progress)

`DahuaLiveLane` turns live targets into `track_update.v1` messages:

- `new` on the first target of a camera-local `ObjectID`;
- `update` at most every 0.5 s per track;
- `end` after 2 s without targets (recorded intra-track gaps: median 0.1 s,
  p99 0.2 s, maximum 1.0 s), or when the collector stops or the camera
  disconnects.

PC receipt time is the timeline. Frames with frame sequence `-1` are
replayed at connection and are dropped and counted. An `ObjectID` that
reappears after its track ended starts a new `track_id` (suffix `:2`, ...),
because ended tracks are never reopened downstream. Recently ended tracks
remain joinable for five minutes so the finalized HumanTrait photos can be
attached by `ObjectID`. Wiring into `live.py` is the next step. The payload is capped per callback
(256 KiB) and in total (512 MiB), and the in-memory queue is bounded. Drops
and write errors are counted in `summary.json` rather than hidden.
