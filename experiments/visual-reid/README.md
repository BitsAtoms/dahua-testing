# Visual tracking experiment

This experiment builds anonymous person continuity from source-specific camera
data. It does not name people, create a watchlist or treat a camera-local ID as
a persistent identity.

The active plan and acceptance gates live in [ROADMAP.md](ROADMAP.md). Concise
evidence from completed experiments lives in
[VALIDATION_HISTORY.md](VALIDATION_HISTORY.md); it is not required reading for
normal implementation work.

## Architecture

```text
Dahua collector ── native events, metadata and crops ──┐
                                                       ├─ normalized evidence
Frigate adapter ─ MQTT lifecycle events and snapshots ─┘          │
                                                                  │
RTSP frame source -> detector -> local tracker provider -----------┤
                                                                  v
                                                     local tracklets
                                                                  │
                                             ReID + time + topology
                                                                  v
                                              provisional sequences
```

The collectors and the future video tracker have different responsibilities:

- Dahua and Frigate preserve source events, identifiers and native media.
- The video tracker maintains stable anonymous IDs inside each camera.
- The tracking engine relates local tracklets across cameras and may return
  `pending` or `unknown`; it must not force an identity decision.
- Source events enrich and audit video tracks. They are too sparse to replace
  regular frame-level detections required by a local tracker.

## Current state

- Dahua and Frigate events are normalized without discarding source IDs.
- Native and adaptive body/face media can be correlated to a local track.
- RTSP buffers are bounded and can raise capture rate while evidence is weak.
- Face and body templates are built from quality-ranked observations.
- Detector consensus runs in shadow mode and never suppresses a live track.
- Norfair and Open Model Zoo were evaluated only as isolated tracker
  baselines. Neither meets the two-person acceptance gate.
- Roboflow BoT-SORT and Deep SORT Realtime/OpenVINO are implemented behind the
  neutral provider contract, but both failed the Meetings fragmentation gate.
- BoxMOT is technically current but blocked by its AGPL-3.0 license pending an
  explicit product decision.
- DeepStream 9.1 is the next candidate. Docker can expose this host's RTX 3050
  to Linux/CUDA containers; no candidate is connected to the live stack yet.

The local supervisor remains the supported way to run the current services.
See [the supervisor README](../../services/local-supervisor/README.md).

## Data and privacy boundary

All processing is local. Do not commit captures, embeddings, credentials,
camera URLs or named biometric galleries. Runtime media follows seven-day
retention. Embeddings are transient unless an explicit product and privacy
decision changes that contract.

Generated data and local configuration belong under ignored paths:

```text
experiments/visual-reid/models/
experiments/visual-reid/output/
experiments/visual-reid/adaptive-capture.local.json
runtime/
```

## Environment and models

Create an isolated environment and download the pinned model files:

```powershell
python -m venv experiments/visual-reid/.venv
experiments/visual-reid/.venv/Scripts/python.exe -m pip install `
  -r experiments/visual-reid/requirements.txt
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/download_models.py
```

`model-manifest.json` records model source, license, size and checksum. Model
weights stay outside Git. The active providers use OpenVINO on CPU; hardware
acceleration must remain behind a provider boundary.

The previous Norfair/Open Model Zoo comparison uses the separate
`.venv-norfair` environment and `requirements-norfair.txt`. These dependencies
are temporary benchmark tooling and must not enter the supervisor environment.

Current local-tracker candidates, revisions, licenses and decisions are
recorded in `local-tracker-sources.json`. The Python baselines use the separate
`.venv-trackers` environment and `requirements-local-tracker.txt` because
their NumPy requirement is intentionally isolated from the evidence stack.

## Adaptive capture

Copy `adaptive-capture.example.json` to the ignored
`adaptive-capture.local.json`, define its URL environment variables in `.env`,
and run:

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/adaptive_capture_service.py --verbose
```

The service maintains bounded per-camera JPEG buffers, correlates live event
boxes to nearby frames and keeps only the best temporally diverse body/face
crops. It creates RTSP crops only from live `new` or `update` observations;
terminal and finalized events must use source-correlated native media.

The current frame buffers are the intended starting point for a shared frame
source. The BoT-SORT integration must reuse or replace them deliberately so the
application does not create multiple independent RTSP decoders per camera.

## Evidence and sequence tools

Audit retained media:

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/audit_media.py
```

Compare anonymous body or face evidence without persisting embeddings:

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/compare_tracks.py TRACK_A TRACK_B
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/compare_faces.py TRACK_A TRACK_B
```

Resolve a labelled validation session:

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/resolve_sequences.py SESSION_ID
```

These scores are evidence channels, not calibrated probabilities. Missing or
low-quality evidence remains neutral.

## Detector and tracker benchmarks

`capture_detector_benchmark.py` records bounded clips from URLs supplied by
environment variable. It never writes the URL into the capture manifest.

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/capture_detector_benchmark.py `
  --camera reuniones_terminator=VISUAL_REID_RTSP_REUNIONES_TERMINATOR `
  --scenario two_person_crossing --expected-person mixed --duration 90
```

Existing detector, Norfair and Open Model Zoo scripts remain only to reproduce
the baseline summarized in `VALIDATION_HISTORY.md`. The BoT-SORT experiment
must consume regular per-frame detections from its intended detector. It must
not be judged only from the sparse high-confidence consensus export, because
that removes weak detections used to bridge occlusions.

## Validation discipline

Every tracker candidate is tested first on immutable recordings. A live
integration starts in shadow mode and cannot affect occupancy or handoffs.

Minimum local-tracker cases:

1. empty scene and persistent non-person interference;
2. one moving and one seated person;
3. two people sequentially;
4. two people crossing and partially occluding;
5. detector gaps and reappearance;
6. RTSP interruption and restart.

The two-person gate requires exactly two identity-pure tracks and no observed
ID switch. Final deployment still needs a short site acceptance test because
camera height, lighting and occlusion patterns change detector coverage.

## Implementation rules

- Keep source adapters separate from normalized tracking logic.
- Preserve raw source payloads and local IDs for audit.
- Define a provider-neutral local tracker contract before live integration.
- Decode each RTSP stream once inside our application boundary.
- Never count a source event and its correlated video track as two occupants.
- Version model and policy changes in retained evidence.
- Prefer `pending` over a weak or contradictory identity assignment.
