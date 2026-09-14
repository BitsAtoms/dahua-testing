# Dahua Research Notes

## Objective

Build a reusable collector for Dahua AI camera events and snapshots as a foundation for a multi-camera monitoring system (the "Batcomputer" concept).

The target system should eventually:

- Receive AI events from multiple cameras and spaces.
- Estimate how many people are present in each space.
- Track local camera detections and correlate likely handoffs between adjacent cameras.
- Show people/spaces on a simple 2D floor map in near real time.
- Preserve event snapshots such as face, body crop, and panoramic/context images.
- Mix Dahua cameras with Hikvision and Eufy streams later, normalizing all sources into one internal event model.

Do not assume that one camera-local track ID is a persistent identity across appearances or across cameras.

---

## Camera currently under test

- Model: `DH-IPC-HDBW7459Z-Z-PV-X`
- Test IP: `192.168.1.XXX`
- RTSP port: `554`
- Main stream example:

```text
rtsp://USER:PASSWORD@192.168.1.XXX:554/cam/realmonitor?channel=1&subtype=0
```

- Substream example:

```text
rtsp://USER:PASSWORD@192.168.1.XXX:554/cam/realmonitor?channel=1&subtype=1
```

The camera is configured in PAL / 50 Hz for the current environment.

---

## Confirmed AI mode used for testing

The current tests use:

```text
AI -> Smart Plan -> Video Metadata
```

For controlled tests, only the person-detection Video Metadata rule should be enabled where possible.

Confirmed Video Metadata categories available in the UI include:

- Person detection
- Motor vehicle detection
- Non-motor vehicle detection

The camera also exposes face-related metadata while Video Metadata is running.

---

## Confirmed event endpoint

The following Dahua CGI endpoint works on the camera and provides a persistent event stream:

```text
http://192.168.1.XXX/cgi-bin/eventManager.cgi?action=attach&codes=[All]
```

Use Digest authentication. Example on Windows PowerShell:

```powershell
curl.exe --digest -u "admin:PASSWORD" --no-buffer --globoff `
  "http://192.168.1.XXX/cgi-bin/eventManager.cgi?action=attach&codes=[All]"
```

Equivalent URL-encoded form:

```text
http://192.168.1.XXX/cgi-bin/eventManager.cgi?action=attach&codes=%5BAll%5D
```

Important: `--globoff` is required if literal square brackets are used with curl, otherwise curl may report `bad range in URL`.

---

## Confirmed event type

The main event observed during person detection is:

```text
Code=HumanTrait
```

Typical event wrapper:

```text
Code=HumanTrait;action=Start;index=0;data={...}
```

and later:

```text
Code=HumanTrait;action=Stop;index=0;data={...}
```

The event stream is multipart text with boundaries such as:

```text
--myboundary
Content-Type: text/plain
Content-Length: ...
```

A controlled capture showed only `Content-Type: text/plain` parts. No `image/jpeg` part was present in this CGI stream.

Therefore:

- CGI event metadata is confirmed.
- Images are not embedded in this specific `eventManager.cgi` attach stream.

---

## Confirmed metadata fields

The camera provides rich metadata, including fields such as:

```text
EventID
EventSeq
EventUUIDStr
FrameSequence
GroupID
RuleID
Name
ObjectID
BelongID
RelativeID
ObjectType
BoundingBox
OriginalBoundingBox
Center
HumanRect
UTC
RealUTC
WithSnap
```

It also reports body / appearance attributes such as:

```text
BackBag
Bag
CarrierBag
ShoulderBag
HasHat
HasMask
Helmet
Phone
Umbrella
UpClothes
DownClothes
UpperBodyColor
LowerBodyColor
UpperPattern
HairStyle
Sex / Gender
```

and face-related attributes such as:

```text
Age
Angle
Beard
Complexion
Emotion
FaceAlignScore
FaceQuality
Feature
Glass
Mask
Sex
```

Treat inferred semantic attributes as low-confidence supporting signals, not as authoritative identity data.

---

## Confirmed local tracking behavior

The controlled single-person test showed that `ObjectID` is a temporary local track ID.

Example sequence while the same physical person repeatedly entered and left the camera view:

```text
140
141
143
144
```

The same physical person was assigned a new local ID after leaving and re-entering.

Conclusion:

```text
ObjectID != persistent person identity
```

Recommended internal interpretation:

```text
(camera_id, ObjectID) = local track
```

Example:

```text
(dahua_213, 144)
```

A separate tracking / ReID layer must decide whether tracks from different appearances or cameras correspond to the same physical person.

---

## Confirmed body <-> face relationship

The controlled test showed an explicit bidirectional relationship between the body object and face object.

Example:

```text
Body
ObjectID   = 144
RelativeID = 1000144
```

Associated face:

```text
HumanFace
ObjectID   = 1000144
BelongID   = 144
RelativeID = 144
```

The same pattern was observed for other tracks, for example `140` / `1000140` and `143` / `1000143`.

This can be modeled as:

```text
LOCAL TRACK 144
|
+-- body object
|   +-- ObjectID = 144
|
+-- face object
    +-- ObjectID = 1000144
    +-- BelongID = 144
    +-- RelativeID = 144
```

For current experiments, the body `ObjectID` should be treated as the main local track ID.

### Controlled two-person result

A controlled test with two people visible at the same time but kept on opposite
sides of the image produced exactly two complete local tracks:

```text
track 4573 -> body GroupID 1724 -> face object 1004573
track 4574 -> body GroupID 1726 -> face object 1004574
```

Each track recovered one body JPEG, one face JPEG, one panoramic JPEG and one
face-panoramic JPEG. Reciprocal `RelativeID`/`BelongID` relationships were
correct, the bounding boxes remained on opposite sides of the scene, and
visual inspection found no body/face association swapped between subjects.
The two NetSDK callbacks produced eight JPEGs in total. This validates the
separated multi-person case.

### Controlled crossing and occlusion result

A later test had the same two people enter from opposite sides, cross with a
brief partial occlusion, continue towards the opposite sides and leave the
scene. The camera emitted four completed local tracks for the two physical
people:

```text
person A -> track 4577 (body + face) and track 4579 (body only)
person B -> track 4578 (body + face) and track 4581 (body only)
```

The four NetSDK callbacks produced twelve JPEGs. Visual inspection confirmed
that the face and body crops within the two face-bearing tracks belonged to
the correct subjects; there was no observed cross-person body/face swap. The
two body-only tracks matched the clothing and appearance of the corresponding
face-bearing tracks.

This proves track fragmentation around crossing/occlusion on the current
camera configuration. A `(camera_id, ObjectID)` pair therefore represents a
camera-local track segment, not necessarily a complete uninterrupted visit by
one person. A downstream tracking layer must be able to merge plausible
segments using visual appearance, time, image geometry and later camera
topology. Since this test used completed `HumanTrait` events rather than a
continuous metadata stream, it does not prove whether an ID swap occurred at
any intermediate frame; it only proves the final fragmentation and recovered
associations described above.

---

## Snapshot findings

Events commonly contain:

```text
WithSnap = true
```

The Dahua Web 5.0 Metadata live view visibly shows event snapshots.

Observed snapshot types include:

- Human body crop
- Face crop
- Panoramic / context image

The locally saved filenames appear to include:

- Snapshot type
- Timestamp
- Local track ID

Examples observed conceptually:

```text
...Human body..._144.jpg
...Face..._144.jpg
...Human body-Panoramic image..._144.jpg
```

Important finding: even though the face object can have `ObjectID = 1000144`, the face JPG filename uses the body/local track ID suffix `_144`.

This matches the `BelongID` / `RelativeID` relationship and suggests the body/local track ID is the event grouping key used for saved snapshots.

---

## Web 5.0 reverse-engineering findings

Browser DevTools showed repeated calls to:

```text
POST http://192.168.1.XXX/RPC2
```

Observed calls were mainly polling camera state, for example hardware/alarm state operations. These were not the snapshot transport path.

In Metadata live view, snapshots are displayed through browser-generated URLs such as:

```text
blob:http://192.168.1.XXX/<uuid>
```

Important:

- A `blob:` URL is browser-local and temporary.
- It is not an HTTP endpoint that can be requested directly from curl or the backend.
- The browser is constructing the blob from binary data already received through another mechanism.
- DevTools did not expose a clean reusable HTTP image endpoint during the current inspection.

Do not spend additional time trying to call the `blob:` URL from the backend.

---

## Current technical conclusion

The Dahua camera already performs a significant amount of useful edge AI:

```text
video
 -> person detection
 -> local tracking
 -> face association
 -> body/face attributes
 -> snapshots
 -> event metadata
```

For Dahua cameras, this suggests avoiding unnecessary duplicate GPU inference for basic detection when the native camera metadata is sufficient.

Frigate may still be useful for:

- Hikvision / Eufy cameras that do not provide equivalent metadata.
- Recording and unified video management.
- Additional object detection.
- Face recognition / embeddings.
- ReID assistance.
- A normalized source for non-Dahua cameras.

---

## Proposed normalized event model

The eventual collector should normalize Dahua metadata into something similar to:

```json
{
  "source": "dahua",
  "camera_id": "dahua_213",
  "local_track_id": 144,
  "face_object_id": 1000144,
  "object_type": "Human",
  "event_id": 10826,
  "event_uuid": "...",
  "timestamp": "...",
  "bounding_box": [0, 0, 0, 0],
  "center": [0, 0],
  "attributes": {},
  "with_snapshot": true,
  "snapshots": {
    "body": null,
    "face": null,
    "panoramic": null
  }
}
```

The exact schema is not final.

The implemented boundary now separates the enriched source record from the
tracking message:

```text
observation.v1
  -> complete Dahua correlation, media references and raw source evidence

track_update.v1
  -> provider-neutral, lightweight input for the tracking engine
```

`track_update.v1` supports `new`, `update`, `end` and `snapshot`. Frigate can
provide the live three-phase lifecycle. The current Dahua `HumanTrait` adapter
uses `snapshot` with `source_lifecycle=finalized_only`, because the tested
firmware publishes the useful event at track finalization. Its `track_id` is a
stable identifier for that source track segment, not a persistent person ID.
Cross-camera identity and same-camera fragment merging remain responsibilities
of the downstream tracking engine.

A real-camera validation produced three progressive `track_update.v1`
revisions for local track `4583`. All shared one `track_id`, sequences advanced
from 1 to 3, the final revision contained body, face and both panoramic media,
and its quality became `complete`. The Dahua bounding box and center were
converted from the native 0..8192 coordinate system to `normalized_0_1`. The
first revision was published about 8.9 seconds after the camera timestamp
(track lifetime), while enrichment completed roughly 230 ms later. This
confirms the connector does not add the multi-second delay.

---

## Target system architecture

High-level concept:

```text
Dahua cameras
  |-- native AI metadata/events
  |-- RTSP
  v
Dahua Event Collector
  |
  +-------------------+
                      |
Hikvision / Eufy      |
  |                   |
  +-> RTSP -> Frigate |
                      |
                      v
              Event Normalizer
                      |
                      v
                Tracking Engine
                      |
          +-----------+-----------+
          |                       |
          v                       v
     Persistence             WebSocket/API
          |                       |
          v                       v
   snapshots/events          Batcomputer UI
                               2D floor map
```

The tracking engine will eventually create a persistent application-level ID such as:

```text
global_person_id = P0037
```

from multiple temporary local tracks.

---

## Cross-camera correlation strategy

Implement progressively.

### Phase 1 - Spatial / temporal handoff

Use:

- camera adjacency
- time between disappearance and next appearance
- allowed physical transitions
- direction of movement

### Phase 2 - Attribute assistance

Use camera-provided attributes as supporting evidence:

- clothing color / type
- bag / backpack
- hat
- other stable appearance cues

Do not rely heavily on:

- estimated age
- estimated sex/gender
- emotion

These values changed across controlled captures of the same person.

### Phase 3 - Visual ReID

Add body and/or face embeddings on the central GPU server.

Possible score inputs:

```text
body embedding similarity          very high weight
face embedding similarity          very high weight
camera adjacency                   high weight
time between detections            high weight
direction                          high weight
clothing / bag attributes          medium weight
age / sex estimates                low weight
emotion                            negligible weight
```

---

## 2D map limitation

Camera metadata provides image coordinates such as `BoundingBox` and `Center`, not automatically real-world building coordinates.

Initial UI should represent people by camera/zone rather than pretending to know exact metric coordinates.

Later, image-to-floor projection may be approximated using camera calibration / homography where the scene geometry allows it.

---

## Next experiment: Dahua NetSDK Win64

The next immediate goal is to determine whether Dahua NetSDK can deliver the AI event together with image buffers for the associated snapshots.

Do not integrate this into the main project yet.

Create an isolated experiment first:

```text
experiments/dahua-netsdk/
```

Minimum success criteria:

```text
1. Initialize NetSDK
2. Login to 192.168.1.XXX
3. Subscribe to intelligent events / pictures
4. Detect HumanTrait / Video Metadata events
5. Print useful event fields
6. Receive image buffer(s)
7. Save body / face / panoramic JPEGs if provided
8. Correlate saved images with the local track / event IDs
9. Cleanly unsubscribe and logout
```

Research current official Dahua NetSDK Win64 packages before choosing binaries. Old public GitHub mirrors can be used as API examples but should not be assumed to be the correct production SDK version.

Relevant NetSDK APIs to investigate include intelligent-event picture subscription functions such as `CLIENT_RealLoadPictureEx` or the current equivalent in the downloaded SDK.

Do not assume function names, struct layouts, event constants, or callback buffer formats until confirmed against the actual SDK headers/docs being used.

---

## Security / credentials

Never commit real camera credentials.

Use environment variables or a local ignored config file, for example:

```text
DAHUA_HOST=192.168.1.XXX
DAHUA_PORT=37777
DAHUA_USER=admin
DAHUA_PASSWORD=...
```

Check the actual NetSDK service port before assuming `37777`; use the camera configuration / SDK documentation as the source of truth.

Add secrets and generated snapshots to `.gitignore` where appropriate.

---

## Important experimental rules

- Keep raw logs from controlled tests.
- Record whether a test involved one person or multiple people.
- Do not infer identity continuity from uncontrolled multi-person logs.
- Distinguish confirmed behavior from hypotheses.
- Preserve raw event JSON/text before normalization.
- Never silently change camera configuration while testing an ingestion hypothesis.
- Keep experiments isolated from the production Digital Twin backend until the data path is proven.

---

## Current status summary

Confirmed:

```text
[YES] RTSP connectivity
[YES] Video Metadata person detection
[YES] CGI persistent event subscription
[YES] HumanTrait events
[YES] local ObjectID tracking
[YES] body <-> face association
[YES] bounding boxes / centers
[YES] rich body attributes
[YES] rich face attributes
[YES] WithSnap=true
[YES] Web 5.0 displays face/body snapshots
[YES] local snapshots use the local track ID as a grouping suffix
[NO] persistent identity after leaving/re-entering
[NO] cross-camera identity from camera-local ObjectID
[NO] JPEG embedded in tested eventManager.cgi stream
[YES] programmatic retrieval of native Dahua AI snapshots
[YES] NetSDK image callback test
[YES] live CGI + NetSDK hybrid correlation
[PENDING] multi-camera normalized collector
[PENDING] cross-camera ReID
```

---

## Confirmed NetSDK callback findings

The official Win64 NetSDK package in this repository was validated against the
real camera using `CLIENT_LoginWithHighLevelSecurity` and
`CLIENT_RealLoadPictureEx` with image delivery enabled.

The same official header also exposes
`CLIENT_AttachVideoAnalyseTrackProc`. Its callback structure contains a list
of live video targets with object UUID, object type and an 8192-based bounding
box. This is now the primary candidate for the low-latency tracking lane, but
support by the current IPC is not yet confirmed. The experiment can request it
as an optional parallel subscription with `DAHUA_LIVE_TRACK_PROBE=1`; it is
disabled by default and failure does not interrupt `HumanTrait`.

A controlled real-camera probe subsequently returned a valid subscription
handle, but produced zero track callbacks while one person moved in view for
about 12 seconds. `live-track-updates.jsonl` remained empty while the normal
`HumanTrait` event arrived with body, face, panoramic and face-panoramic JPEGs.
For this IPC/firmware/configuration, accepting the attach request therefore
does not prove that the live track feed is implemented or enabled.

The main RTSP URL was also inspected through its SDP. It advertised one HEVC
video stream and no ONVIF/application metadata stream. Thus the ordinary RTSP
URL cannot currently provide native bounding boxes alongside the video.

Confirmed callback results:

```text
[YES] EVENT_IVS_HUMANTRAIT received
[YES] native human body JPEG received
[YES] native face JPEG received
[YES] native panoramic/context JPEG received
[YES] native face-associated panoramic JPEG received
[YES] all image offsets and lengths fit the callback buffer
[YES] unsubscribe, logout, and SDK cleanup completed without crash
```

One callback containing all four image types reported:

```text
buffer size:             672089 bytes
human body:              1104 x 2160
face:                     928 x 928
panoramic/context:       3840 x 2224
face panoramic/context: 3840 x 2224
```

### NetSDK and CGI identifier correlation

For this camera/firmware, `DEV_EVENT_HUMANTRAIT_INFO.nObjectID` and
`nEventID` were zero in the NetSDK callback. They must not be treated as usable
local identifiers.

A simultaneous CGI and NetSDK capture produced four exact `GroupID` matches:

```text
NetSDK GroupID 216 <-> CGI GroupID 216 <-> CGI ObjectID 961
NetSDK GroupID 217 <-> CGI GroupID 217 <-> CGI ObjectID 962
NetSDK GroupID 218 <-> CGI GroupID 218 <-> CGI ObjectID 963
NetSDK GroupID 219 <-> CGI GroupID 219 <-> CGI ObjectID 965
```

Confirmed interpretation for this firmware:

```text
GroupID       = cross-channel correlation key for the capture group
CGI ObjectID  = camera-local temporary track ID
NetSDK nObjectID = unavailable (zero) in observed callbacks
```

`GroupID` must not be renamed to `local_track_id`; the values are different.
The current experimental collector preserves `GroupID` and requires CGI
correlation to recover the camera-local `ObjectID` when NetSDK reports zero.

A later simultaneous facial capture confirmed the complete association:

```text
NetSDK HumanTrait GroupID 333:
  body JPEG + face JPEG + panoramic JPEG + face panoramic JPEG

CGI body event:
  GroupID=333, ObjectID=1520, RelativeID=1001520, EventID=11112

CGI face event:
  GroupID=334, ObjectID=1001520, BelongID=1520,
  RelativeID=1520, EventID=11113
```

The NetSDK callback carrying all four images correlated directly with the CGI
body event by `GroupID=333`. The associated CGI face event used another group
(`334`) and must be joined through the reciprocal body/face identifiers. This
means the future merger needs both relationships: `GroupID` for the NetSDK to
CGI body match, then `RelativeID`/`BelongID` for the CGI body to face match.

The first combined live collector subsequently emitted normalized events
without an offline comparison pass. One live facial event produced:

```text
camera_id=dahua_213
local_track_id=1559
face_object_id=1001559
body GroupID=362
face GroupID=363
body EventID=11141
face EventID=11142
snapshots=body,face,panoramic,face-panoramic
correlation.status=complete
```

The live run also recovered automatically after an idle CGI read timeout and
shut down the native NetSDK child without leaving it running.

---

## Planned Dahua collector control panel

The collector should eventually include a small local web control panel rather
than requiring operators to infer its state from output files. The UI must be a
client of the collector service; closing the browser must not stop active camera
connections.

Suggested separation:

```text
Windows collector service / supervisor
  -> one isolated camera worker per configured camera
  -> NetSDK + CGI connection lifecycle
  -> status, counters, logs and normalized events
  -> local HTTP API + SSE/WebSocket

Local web control panel
  -> add, edit, enable or disable cameras
  -> connect, disconnect and restart a camera worker
  -> show SDK and CGI status independently
  -> show last event, image thumbnails and correlation state
  -> show reconnect attempts and recent errors
```

Initial camera configuration fields:

```text
nickname / camera_id
host or IP
NetSDK port
HTTP port
username
password secret reference
enabled state
```

The API must never return stored passwords to the browser. For the experiment,
credentials can continue to come from ignored environment files. A packaged
Windows version should use an OS-backed secret store or encrypted-at-rest
credentials rather than plain text configuration.

### Retention requirement

Collector output must be bounded. At the time this requirement was added, 34
JPEG files occupied 6.67 MiB while all JSON and log files together occupied
about 0.06 MiB, confirming that snapshots dominate storage growth.

The product decision is a uniform seven-day lifetime for every collected file,
including snapshots, raw CGI logs, raw NetSDK JSON, normalized JSONL, and
future collector artifacts. The multicamera supervisor applies this policy at
startup and once per hour to its entire output root. No size ceiling currently
removes younger data. The standalone retention command remains a dry-run unless
the operator explicitly supplies `--apply`.
