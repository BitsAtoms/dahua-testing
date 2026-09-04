# AGENTS.md

## Project purpose

This repository contains experiments and implementation work for a multi-camera spatial monitoring system.

The immediate task is to integrate Dahua AI cameras programmatically, starting with a `DH-IPC-HDBW7459Z-Z-PV-X`, and later normalize Dahua, Hikvision, Eufy, and Frigate events into a common tracking backend.

The long-term UI concept is a "Batcomputer" style real-time 2D map of office/showroom spaces, with estimated occupancy, camera handoffs, event history, and associated snapshots.

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

## First priority

Do not start by modifying the main Digital Twin backend.

The current milestone is an isolated Dahua NetSDK proof of concept.

Preferred location:

```text
experiments/dahua-netsdk/
```

The experiment should prove this data path:

```text
Dahua camera
 -> NetSDK login
 -> intelligent event subscription
 -> HumanTrait / Video Metadata event
 -> metadata fields
 -> image buffer(s)
 -> save JPEG(s)
```

Only after this path is proven should it be integrated into the main application.

---

## Camera currently under test

```text
Model: DH-IPC-HDBW7459Z-Z-PV-X
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

The next task is to obtain equivalent image data from NetSDK callbacks or another documented Dahua API.

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

---

## Definition of done for the current milestone

The Dahua NetSDK experiment is successful when all of the following are proven on the real camera:

```text
[ ] SDK loads successfully on Windows
[ ] camera login succeeds
[ ] intelligent event subscription succeeds
[ ] HumanTrait / relevant metadata event is received
[ ] local track ID is printed
[ ] face/body relation is recoverable
[ ] image buffer is received
[ ] at least one native Dahua snapshot is saved as a valid JPEG
[ ] event and JPEG can be correlated to the same detection
[ ] unsubscribe/logout/cleanup works without crash
```

Do not start the production tracking engine until this milestone is complete or explicitly abandoned in favor of a different ingestion mechanism.
