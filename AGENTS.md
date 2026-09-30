# AGENTS.md

## Project purpose

This repository contains experiments and implementation work for a multi-camera spatial monitoring system.

Dahua, Hikvision, Eufy and Frigate events are normalized into a common tracking backend. The Dahua NetSDK proof of concept is complete.

The goal is a "Batcomputer" style real-time 2D map of office/showroom spaces, with estimated occupancy, camera handoffs, event history, and associated snapshots. It must run on one final Windows PC and become operational from a single launch.

The system-level source of truth is the root `ROADMAP.md`. The owner-facing concept guide is `docs/guia.md`.

Before doing Dahua work, read:

```text
dahua-research.md
```

If it exists under `docs/`, use:

```text
docs/dahua-research.md
```

Treat that file as the current research baseline.

---

## Working with the project owner

The owner is not a computer-vision specialist and has asked for a careful,
ordered, didactic process:

- Reply in Spanish. Keep code, identifiers and technical module READMEs in
  English; owner-facing documents (`ROADMAP.md`, `README.md`, `docs/guia.md`)
  are in Spanish.
- Advance one small, explained step at a time. Before a step, say what it is,
  why it is needed and where it fits in the architecture, in plain language.
- Ask for decisions that belong to the owner instead of deciding silently.
- Keep `docs/guia.md` short: only key concepts, explained in depth where they
  really matter. It is not a reference manual.
- Prefer existing, maintained tools over new in-house components.

---

## Deployment target

The final system runs on one Windows 11 PC (AMD Ryzen 7 9850X3D, 64 GB RAM,
about 4 TB disk, two AMD RX 9070 XT). The exact camera inventory and the
operating decisions are recorded in `ROADMAP.md`. Key constraints:

- Dahua cameras use the native Dahua collector (NetSDK + CGI) on Windows.
- Non-Dahua cameras go through Frigate in Docker. Frigate detects on CPU
  because Docker on Windows cannot use the AMD GPUs.
- A GPU-based in-house detector/tracker is optional and only resumes when
  measurements show Frigate is insufficient
  (`experiments/visual-reid/ROADMAP.md`).
- One dedicated Windows user with automatic sign-in starts the whole stack.
  The UI is shown only on the local screen; services bind to localhost.
- Retention is seven days for all collected data; Frigate keeps event clips
  only.

Anything measured in image space (thresholds, masks, travel times, model
choice) is calibrated only after final camera placement. Current recordings
are regression tests, not calibration data.

---

## Reference test camera

```text
Model: DH-IPC-HDBW7859Z-Z4-PV-X   (camera_id dahua_213)
Host:  192.168.1.XXX
RTSP:  554
```

Do not hard-code credentials.

Use environment variables or an ignored local config.

Suggested environment variables:

```text
DAHUA_HOST
DAHUA_PORT
DAHUA_USER
DAHUA_PASSWORD
```

Do not assume the SDK port. Confirm it from the camera configuration or the official SDK documentation before using it.

---

## Known-good metadata endpoint

The camera has already been verified with:

```text
/cgi-bin/eventManager.cgi?action=attach&codes=[All]
```

using HTTP Digest authentication.

A Windows test command is:

```powershell
curl.exe --digest -u "admin:PASSWORD" --no-buffer --globoff `
  "http://192.168.1.XXX/cgi-bin/eventManager.cgi?action=attach&codes=[All]"
```

This endpoint returns textual multipart event metadata and has produced `HumanTrait` events.

It did NOT provide JPEG parts in the controlled test.

Do not spend time trying to extract JPEGs from this endpoint unless new evidence suggests the firmware supports an alternate attach mode.

---

## Confirmed tracking semantics

`ObjectID` is a camera-local temporary track ID.

It is not a persistent identity.

The same physical person received different IDs after leaving and re-entering the scene.

Use terminology carefully:

```text
local_track_id = camera-local temporary track
global_person_id = application-level correlated identity
```

Never call `ObjectID` a persistent person ID.

---

## Confirmed body / face relation

The camera associates body and face objects.

Typical pattern:

```text
Body:
  ObjectID = 144
  RelativeID = 1000144

Face:
  ObjectID = 1000144
  BelongID = 144
  RelativeID = 144
```

Preserve these fields in raw ingestion.

Do not discard `BelongID`, `RelativeID`, `EventID`, `EventUUIDStr`, `GroupID`, or `FrameSequence` during early implementation.

---

## Raw data first

When building parsers or SDK callbacks:

1. Save or log the raw event structure first.
2. Then map it into a normalized model.
3. Keep unknown fields available for later inspection.
4. Avoid reducing the payload too early.

A future normalized event may look roughly like:

```json
{
  "source": "dahua",
  "camera_id": "dahua_213",
  "local_track_id": 144,
  "face_object_id": 1000144,
  "event_id": 10826,
  "event_uuid": "...",
  "timestamp": "...",
  "object_type": "Human",
  "bounding_box": [],
  "center": [],
  "attributes": {},
  "with_snapshot": true,
  "snapshots": {}
}
```

This is a design sketch, not a locked schema.

---

## NetSDK implementation rules

Use the current official Dahua NetSDK Win64 package whenever possible.

Old public GitHub repositories may be used to understand historical API patterns, but do not copy old DLLs or struct definitions into production without verifying them against the current SDK.

Before coding a NetSDK binding:

1. Inspect the SDK package version.
2. Read the included headers and examples.
3. Identify the exact login API.
4. Identify the exact intelligent-event / picture subscription API.
5. Confirm callback signatures from the headers.
6. Confirm event constants for Video Metadata / HumanTrait.
7. Confirm how image buffers and offsets are represented.

Likely historical API names such as `CLIENT_RealLoadPictureEx` are clues only. The downloaded SDK headers are authoritative.

---

## Preferred experiment output

For each received event, write clear console output such as:

```text
camera=dahua_213
code=HumanTrait
action=Start
local_track_id=144
face_object_id=1000144
event_id=...
with_snap=true
buffer_size=...
```

If image data is available, save it under an experiment output folder:

```text
experiments/dahua-netsdk/output/
```

Suggested naming:

```text
<timestamp>_cam-213_track-144_body.jpg
<timestamp>_cam-213_track-144_face.jpg
<timestamp>_cam-213_track-144_panoramic.jpg
<timestamp>_cam-213_track-144_event.json
```

Do not commit generated captures unless explicitly needed as sanitized test fixtures.

---

## Testing discipline

Distinguish controlled and uncontrolled tests.

For identity / track lifecycle tests:

- Prefer one person in frame.
- Record entry and exit times.
- Note when the person leaves the camera view completely.
- Compare `ObjectID` before and after re-entry.

For multi-person tests:

- Do not infer which event belongs to which physical person unless the mapping is observable.

Whenever possible, preserve raw logs under a test-data directory that is excluded from production builds.

---

## Snapshot assumptions

The Web 5.0 Metadata live view displays:

- body crop
- face crop
- panoramic/context capture

Events report `WithSnap=true`.

Browser inspection found `blob:` URLs for displayed images. These are browser-local URLs, not camera endpoints.

Do not attempt to use those `blob:` URLs in backend code.

Equivalent native image data is now obtained through `CLIENT_RealLoadPictureEx`; see `docs/dahua-research.md`.

---

## Architecture direction

Target architecture:

```text
Dahua cameras
 -> native Dahua AI events / metadata
 -> Dahua collector

Hikvision / Eufy
 -> RTSP
 -> Frigate / other processors

Dahua collector + Frigate
 -> event normalizer
 -> tracking engine
 -> persistence
 -> WebSocket/API
 -> 2D Batcomputer UI
```

Do not force every camera brand through the same low-level ingestion path.

Normalize after source-specific ingestion.

---

## Tracking engine principles

Cross-camera identity should eventually use multiple signals.

Priority order:

```text
1. body / face visual embeddings
2. camera adjacency / topology
3. time between disappearance and appearance
4. direction of travel
5. stable clothing / bag attributes
6. weak demographic estimates
```

Do not use estimated emotion as an identity signal.

Treat age / gender estimates as weak and potentially inconsistent.

---

## 2D map principles

Do not claim exact real-world XY position unless calibration supports it.

Initial implementation should map detections to:

```text
camera -> zone -> room/space
```

and animate likely transitions between adjacent spaces.

Later, calibrated camera geometry / homography may map image coordinates to approximate floor coordinates.

---

## Safety and privacy

This system can process face and body imagery.

Do not add persistent biometric identification, retention policies, or person-naming features without an explicit product decision and legal/privacy review appropriate to the deployment jurisdiction.

Keep experiments scoped to technical validation.

Do not expose camera credentials, raw authentication headers, cookies, or session tokens in committed files or logs.

The GitHub repository is public. Never commit site data: real IP addresses,
RTSP URLs, credentials, recordings, snapshots, embeddings, real floor plans or
local configuration. Use placeholders such as `192.168.1.XXX` in documentation.

---

## Coding expectations

- Prefer small, testable modules.
- Keep source-specific adapters separate from normalized domain logic.
- Avoid hard-coded IPs outside experiment config.
- Add structured logging.
- Fail loudly on SDK loading, login, subscription, or callback errors.
- Always cleanly unsubscribe, logout, and release SDK resources.
- Keep binary SDK files isolated and document their exact version/source.
- Add a README to each experiment describing setup and run commands.
- Do not refactor unrelated project code while proving the NetSDK path.

## Operational notes (learned on the development PC)

- A local Mosquitto broker is usually running. Any test that runs a collector
  or adapter must set `TRACK_MQTT_TOPIC=tracking/test/track-updates` and a
  scratch `TRACK_OUTBOX_ROOT`, or its messages reach the real receiver.
- Frigate and Mosquitto run from `deploy/docker/compose.yml`. The ignored
  `deploy/docker/.env` holds the camera URLs and points
  `STACK_FRIGATE_CONFIG_DIR` at the owner's `frigate-runtime/config` folder.
  That configuration is site data: never commit it. Change it through the
  Frigate UI (the owner applies the edits), or with the owner's explicit
  agreement. A setting toggled at runtime in the Frigate UI may not be saved
  in `config.yaml`; check `/api/config/raw` before assuming it persists.
- Per-camera probe settings go in ignored `.env.<camera_id>` files. Read secret
  files only through code that uses them; never print their values.
- Camera clocks can be wrong (one test camera was 26.9 days behind). Use PC
  receipt time as the primary timeline and keep camera time as evidence.
- The development PC is not the final PC (RTX 3050 here, two AMD RX 9070 XT
  there). Do not report AMD GPU results from this machine.
- Owner-visible docs are in Spanish. When a script writes Windows paths into
  Markdown, check that sequences such as `\r` were not turned into control
  characters.

## Roadmap and Git workflow

The root `ROADMAP.md` is the durable source of truth for the whole system.
Experiment roadmaps such as `experiments/visual-reid/ROADMAP.md` hold detail
for their own track only. At the start of a resumed task, read `ROADMAP.md`,
this file and Git status. Keep the roadmap updated when implementation
evidence changes a phase, checkpoint, risk or next step.

When completing a meaningful checkpoint, tell the user:

1. which phase/checkpoint was completed;
2. what evidence satisfied its exit criterion;
3. the current phase and immediate next step;
4. whether the branch is ready for review or merge.

Prefer branches per coherent, reviewable functionality rather than one branch
per entire roadmap phase. A phase may contain multiple feature branches. Use
the `codex/` prefix and a descriptive name such as
`codex/visual-reid-adaptive-face`. Keep the current branch while its working
tree contains one coherent unfinished checkpoint; do not create artificial
branches only to mirror roadmap headings.

Recommend merging to `main` only at a solid checkpoint where:

- the feature's automated tests and relevant integration checks pass;
- its controlled live validation exit criterion has passed;
- generated media, credentials and local configuration remain ignored;
- documentation and the roadmap reflect the observed behavior and limits;
- the diff is reviewable and no known correctness issue requires an immediate
  follow-up in the same change.

Do not merge automatically. Present the checkpoint and recommendation to the
user, and wait for explicit authorization before merging to `main`.

Pushing to the public `origin` also requires explicit user confirmation.
Before any push, scan the outgoing diff for site data and credentials.

---

## Completed milestone: Dahua NetSDK proof of concept

All criteria were proven on the real camera; evidence is in
`docs/dahua-research.md`.

```text
[x] SDK loads successfully on Windows
[x] camera login succeeds
[x] intelligent event subscription succeeds
[x] HumanTrait / relevant metadata event is received
[x] local track ID is printed (recovered from CGI ObjectID via GroupID)
[x] face/body relation is recoverable
[x] image buffer is received
[x] at least one native Dahua snapshot is saved as a valid JPEG
[x] event and JPEG can be correlated to the same detection
[x] unsubscribe/logout/cleanup works without crash
```

Known limit: on the tested firmware `HumanTrait` is published when the
camera-local track ends, so it cannot drive live positions on the map.
