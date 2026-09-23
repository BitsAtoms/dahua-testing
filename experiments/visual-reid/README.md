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
- BoxMOT 25.0.0 is available behind the same neutral contract for this
  internal, non-commercial demo. Its four tested ReID trackers did not pass
  the Meetings gate: BoT-SORT fragmented one person, while OccluBoost made an
  unsafe person-to-interference merge.
- DeepStream 9.1 is the parallel integrated-pipeline benchmark. Docker can
  expose this host's RTX 3050 to Linux/CUDA containers. After the Windows
  driver update, PeopleNet Transformer + NvDeepSORT and NvDCF both completed
  the Meetings replay faster than its 20 FPS source. The official NvDCF
  configuration produced six clean fragments; a bounded re-association variant
  reduced this repeatably to four without mixing the two people. It also
  rejected the Reception robot and the empty Meetings room, but still failed
  pose coverage and continuity cases. Repeating the crossing from the better
  Reception view gave three repeatable tracks: one person stayed stable and the
  other split once after partial occlusion/out-of-frame movement. Camera
  geometry therefore matters, but does not fully solve fragmentation. It is
  the best integrated reference, not yet a passing provider. No candidate is
  connected to the live stack yet.

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

The same manifest pins the NGC PeopleNet Transformer and ReIdentificationNet
files used by DeepStream. TensorRT engines are generated locally for the host
GPU and remain under the ignored `models/deepstream/` directory.

RT-DETR Warehouse and PeopleNet Transformer v2 use NVIDIA's TAO D-DETR parser.
Build the pinned Apache-2.0 parser once; its source checkout, binary and build
container remain outside Git:

```powershell
experiments/visual-reid/build_deepstream_tao_parser.ps1
```

The previous Norfair/Open Model Zoo comparison uses the separate
`.venv-norfair` environment and `requirements-norfair.txt`. These dependencies
are temporary benchmark tooling and must not enter the supervisor environment.

Current local-tracker candidates, revisions, licenses and decisions are
recorded in `local-tracker-sources.json`. The Python baselines use the separate
`.venv-trackers` environment and `requirements-local-tracker.txt` because
their NumPy requirement is intentionally isolated from the evidence stack.

BoxMOT evaluation uses another isolated environment. Its AGPL-3.0 dependency
is authorized only for the internal demo described above; reconsider the
license before distribution, hosted access or commercial use.

```powershell
python -m venv experiments/visual-reid/.venv-boxmot
experiments/visual-reid/.venv-boxmot/Scripts/python.exe -m pip install `
  -r experiments/visual-reid/requirements-boxmot.txt
```

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
source. Any accepted tracker integration must reuse or replace them
deliberately so the application does not create multiple independent RTSP
decoders per camera.

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
the baseline summarized in `VALIDATION_HISTORY.md`. Local-tracker experiments
must consume regular per-frame detections from their intended detector. They
must not be judged only from the sparse high-confidence consensus export,
because that removes weak detections used to bridge occlusions.

Run the pinned DeepStream 9.1 GPU benchmark and convert its official
`kitti-track-output-dir` files into a JSON report:

```powershell
experiments/visual-reid/run_deepstream_benchmark.ps1 -Tracker NvDCF
experiments/visual-reid/run_deepstream_benchmark.ps1 -Tracker NvDeepSORT
experiments/visual-reid/run_deepstream_benchmark.ps1 -Tracker NvDCFReassoc
experiments/visual-reid/run_deepstream_benchmark.ps1 -Tracker NvDCFReassoc `
  -Detector RTDETR -Recording PATH_TO_RECORDING
experiments/visual-reid/run_deepstream_benchmark.ps1 -Tracker NvDCFReassoc `
  -Detector PeopleNetV2 -Recording PATH_TO_RECORDING
```

The configs pass only PeopleNet's `Person` class to the tracker. Optional
GStreamer warnings for audio/DVD, Triton, Rivermax and UCX plugins do not apply
to this file-video pipeline. `NvDCFReassoc` changes only the official NvDCF
accuracy configuration's shadow age, tracklet-search window and ReID extraction
interval. It is a bounded benchmark, not a production calibration.
The runner replaces only the selected scenario/tracker's generated KITTI
directory before each replay so results cannot inherit stale detections.
The alternate detectors are bounded offline candidates. RT-DETR and PeopleNet
v2 both failed the Reception interference gate and are not enabled in the live
stack.

Compare a candidate's track boxes with detections from an independent run:

```powershell
experiments/visual-reid/.venv/Scripts/python.exe `
  experiments/visual-reid/analyze_deepstream_confirmation.py `
  --tracks PATH_TO_CANDIDATE_TRACKS `
  --detections PATH_TO_CONFIRMING_DETECTIONS --iou 0.3
```

This report measures generic temporal confirmation only. A successful single
recording does not authorize suppression or occupancy changes in the live
stack.

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
