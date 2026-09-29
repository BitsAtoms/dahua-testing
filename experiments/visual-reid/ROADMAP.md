# Visual tracking roadmap

> **Status 2026-09-28: optional track.** The system-level plan is the root
> [`ROADMAP.md`](../../ROADMAP.md). On the final PC, Frigate (Docker, CPU)
> detects and tracks the non-Dahua cameras. The in-house GPU detector/tracker
> below is phase 8 there and resumes only if measurements show Frigate is
> insufficient in quality or CPU capacity. Evidence gathered here remains
> valid, and the retained recordings are regression tests.

This is the durable source of truth for anonymous local and cross-camera
tracking. Detailed historical measurements are kept in
[VALIDATION_HISTORY.md](VALIDATION_HISTORY.md), not in this file.

Status: `[x]` complete and validated, `[~]` implemented but still under live
validation, `[ ]` not implemented.

## Working protocol

- Start resumed work by reading this file, `AGENTS.md` and Git status.
- Change status only from automated or controlled live evidence.
- Keep source adapters, local tracking and cross-camera association separate.
- Use a branch per reviewable integration checkpoint.
- Do not merge without passing tests, the phase exit criterion, clean ignored
  runtime data and explicit user authorization.

Last active branch: `codex/rfdetr-medium-detector`, merged into `main` at
`e6f00f5`. No branch is currently active for this track.

Current decision: stop designing another frame-to-frame tracker. Roboflow
BoT-SORT and Deep SORT Realtime are reproducible providers but fail the
Meetings gate. BoxMOT is authorized for this internal demo, but all four tested
ReID trackers also failed: its BoT-SORT is the least unsafe result and still
fragments one person, while OccluBoost merges people with persistent false
positives. DeepStream 9.1 PeopleNet + NvDCF is now the best integrated
reference: it removed persistent stationary interference and its bounded
re-association variant repeatably reduced the two-person run from six to four
apparently identity-pure fragments. It still fails the two-track fragmentation
gate and weak-pose coverage case. A repeatable Reception run improved this to
three tracks, confirming camera geometry matters without eliminating the
underlying fragmentation. RT-DETR Warehouse regressed both interference and
fragmentation. PeopleNet Transformer v2 preserved two stable human IDs but
also tracked the robot. An offline v1 confirmation gate retained both human
tracks and rejected both robot fragments with zero robot confirmations in
Reception, but passed only five of seven retained cases: seated and Meetings
crossing still fragmented people. NvDeepSORT produced nine tracks. Dahua and
Frigate events remain source evidence and audit inputs.

Deployment decision: retain every DeepStream result as benchmark evidence, but
do not integrate it into the live stack. The final computer has two AMD RX 9070
XT GPUs, no NVIDIA GPU, remains on Windows and must not depend on Ubuntu, ROCm
multi-GPU under WSL or Docker GPU passthrough. The next provider must therefore
run in native Windows processes. Windows ML with MIGraphX was the first
candidate. It worked on RX index 0 but failed on RX index 1; ONNX Runtime with
DirectML passed the native Windows hardware gate on both RX cards. Each camera
worker will eventually own one explicitly selected GPU; the two cards are not
shared memory or CrossFire. The current checkpoint selects a portable ONNX
person detector on immutable recordings. Tracker and ReID selection remain
deferred until that detector passes its own gate.

## 0. Source ingestion and evidence — complete

- [x] Normalize Dahua and Frigate events without treating their local IDs as
  persistent identities.
- [x] Preserve native metadata, lifecycle and event-correlated media.
- [x] Maintain bounded RTSP buffers and quality-ranked adaptive observations.
- [x] Build versioned face/body evidence and re-evaluate late media.
- [x] Record labelled validation sessions and exclude false positives or
  out-of-scope people from identity metrics.
- [x] Keep generated media and local configuration ignored with bounded
  retention.

Exit evidence: controlled sessions produced auditable source events, crops,
quality results and handoff candidates. Face/body availability limitations are
explicit rather than converted into fabricated scores.

## 1. False-positive containment — shadow validated

- [x] Benchmark downloaded person detectors against persistent interference.
- [x] Implement source-neutral SSD+YOLOX agreement in shadow mode.
- [x] Require temporal consensus before eligibility or exclusion.
- [x] Mark contradictory committed evidence as contamination instead of
  silently changing identity.
- [ ] Decide whether the local tracker detector supersedes this consensus or
  consumes it only as an auxiliary validity signal.
- [ ] Enable suppression only after the chosen tracker passes the negative and
  concurrent-person gates.

The current shadow policy rejected the Reception robot without suppressing the
nearby real person. It is not a production classifier and must not be coupled
directly into BoT-SORT before the complete detector/tracker pipeline is tested.

## 2. Local tracker provider — Windows hardware gate passed; pipeline deferred

- [x] Record exact tracker sources, revisions, licenses and reproducible
  isolated environments in `local-tracker-sources.json`.
- [x] Define source-neutral `DetectionFrame`, `LocalTrackUpdate` and
  `LocalTrackerProvider` contracts.
- [x] Run full-frame detector + Roboflow BoT-SORT and Deep SORT Realtime/0287
  over the immutable Meetings recording at 10 FPS.
- [x] Implement and benchmark BoxMOT 25.0.0 BoT-SORT, Deep OC-SORT,
  StrongSORT and OccluBoost behind the same provider contract.
- [~] Measure track count, fragmentation, false tracks and latency. GPU/memory
  and full ID-switch metrics remain for DeepStream.
- [ ] Pass the Meetings two-person gate: exactly two identity-pure tracks with
  no observed switch.
- [~] Pass empty, interference, seated, one-person and stream-loss cases. The
  empty, interference and seated recordings pass; one-person continuity and
  stream-loss remain open.
- [x] Reject the tested Roboflow, Deep SORT and BoxMOT configurations for live
  integration while retaining them as reproducible benchmarks.
- [ ] Extend the contract with explicit supersession only if a provider uses
  delayed tracklet merging; never silently rewrite an emitted ID.
- [~] Benchmark DeepStream 9.1 PeopleNet + NvDCF/NvDeepSORT in its official
  GPU container against the complete recording matrix. The retained recordings
  are complete; the crossing, weak-pose and separated-reappearance cases still
  fail, and no stream-loss recording exists yet.

DeepStream remains the best integrated NVIDIA reference, but it cannot be the
deployment provider on the final AMD/Windows machine. Its validation evidence
is preserved unchanged in `VALIDATION_HISTORY.md`.

Exit criterion: one pinned pipeline passes the local test matrix on immutable
recordings and has a clear license/deployment path. Benchmark thresholds are
not presented as universal production calibration.

## 2a. Native Windows ONNX hardware gate — passed via DirectML

- [x] Define a small camera-free ONNX benchmark with CPU correctness tests.
- [x] Enumerate Windows ML catalog providers, ORT EP devices and Windows
  physical display adapters without assuming GPU order.
- [x] Select a specific ORT EP device for MIGraphX or DirectML and reject runs
  where profiling shows total or partial CPU fallback.
- [x] Run two selected devices concurrently in independent processes and emit
  versioned JSONL evidence.
- [x] Pin dependencies, commit the small ONNX fixture and document clean-clone
  setup for Windows.
- [~] On the final computer, map the two discrete RX 9070 XT cards and the
  integrated GPU to provider-local indexes using the inventory and PCI
  location evidence. Provider indexes 0/1/2 and distinct DXGI LUIDs are
  recorded; bus 3 versus bus 6 is not yet correlated to the two RX LUIDs.
- [x] Pass CPU, each RX 9070 XT separately and both RX 9070 XT cards
  simultaneously with MIGraphX; if MIGraphX fails, repeat with DirectML and
  preserve both errors and successful evidence. MIGraphX passed RX index 0
  but failed on index 1 with a missing allocator for DeviceId 1; the DirectML
  fallback passed the complete matrix without profiled CPU fallback.

Exit criterion: a report from the final computer proves which real provider
and physical device executed every run, contains no hidden CPU fallback, and
shows two independent GPU processes succeeding concurrently. This gate does
not select the production detector or local tracker.

## 2b. Portable ONNX person detector — RF-DETR follow-up complete; no survivor

- [x] Keep the existing YOLOX-Tiny ONNX model as a regression baseline rather
  than treating its previous weak coverage as an accepted detector.
- [x] Audit official source, license, export path and preprocessing for one
  stronger portable ONNX candidate (YOLOX-M) before downloading weights.
- [x] Add a source-neutral ONNX Runtime detector adapter with explicit provider
  and provider-local device selection; keep YOLOX decoding model-specific.
- [x] Pin both model files by URL, version, size and checksum. Generated models
  and recordings remain ignored.
- [x] Replay the seven retained immutable scenarios and emit machine-readable
  per-frame detections, coverage proxy, negative-case false positives and latency.
- [x] Reject YOLOX-M for persistent robot detections and weak mixed-pose
  coverage. YOLOX-Tiny also fails the empty-room and mixed-pose gates.
- [x] Export official Apache-2.0 RF-DETR Medium v1.11.0 to ONNX with pinned
  weight/export hashes and model-specific RGB/COCO decoding. Verify ONNX
  inference against upstream PyTorch on a retained frame.
- [x] Screen RF-DETR Medium on all seven recordings at 0.3 and compare 0.4/0.5
  from the per-frame scores. It removes robot false positives and improves
  seated/partial coverage, but still fires on screen people and misses visible
  difficult poses. Keep it as an offline benchmark, not an accepted detector.
- [ ] Validate the surviving detector through DirectML on each RX 9070 XT and
  in two independent processes on the final computer.

The offline CPU matrix at 5 FPS and threshold 0.4 found YOLOX-M detecting the
robot in 128/151 negative frames; YOLOX-Tiny detected a chair in 19/150
empty-room frames. Mixed-pose frame detection was only 86/153 and 79/153,
respectively. These are frame-level detection proxies, not annotated recall.
No model proceeds to the DirectML detector gate. Exact per-frame local evidence
is under ignored `output/onnx-detector/`; the decision summary is in
`VALIDATION_HISTORY.md`.

RF-DETR Medium at 0.3 detects the person in 128/153 mixed-pose frames and all
148 seated/partial frames, with zero robot detections but four screen-person
false-positive frames in the empty room. At 0.5 the screen false positives
disappear but mixed-pose coverage drops to 59/153. The 0.3 run profiles each
recording separately and records only `CPUExecutionProvider` nodes. There is
no operating point in the tested range that meets both negative and difficult
pose cases; the detector remains unselected. This is a frame-detection proxy,
not annotated recall or a DirectML result.

Exit criterion: one clearly licensed ONNX detector passes the retained
negative and person-coverage cases, has reproducible preprocessing and output
decoding, and executes through DirectML on both RX cards without profiled CPU
fallback. This checkpoint does not choose a tracker or connect to live video.

## 3. Shared frame pipeline and shadow integration

- [ ] Expose one bounded frame source per camera for detection, tracking and
  evidence crops.
- [ ] Prevent duplicate RTSP decoders inside the application.
- [ ] Run the accepted provider from the local supervisor in shadow mode.
- [ ] Correlate video tracks with Dahua/Frigate events using time and geometry.
- [ ] Preserve unmatched source events and unmatched video tracks explicitly.
- [ ] Publish metrics for queue depth, dropped frames, inference latency,
  fragmentation and event correlation.
- [ ] Recover cleanly from camera and tracker restarts.

Exit criterion: a bounded live run explains every tracker/source correlation
without changing occupancy, handoffs or raw evidence.

## 4. Local-track authority

- [ ] Select which normalized fields are authoritative from the video tracker
  and which remain source-native evidence.
- [ ] Prevent a correlated event and video track from becoming two occupants.
- [ ] Keep source IDs as aliases attached to the video tracklet.
- [ ] Split or quarantine abrupt within-track appearance contamination.
- [ ] Fall back to source events when the video tracker is unavailable.
- [ ] Enable the new local IDs only after controlled live validation.

Exit criterion: local occupancy remains correct through crossings,
interference, missing events and restarts, with a reversible audit trail.

## 5. Deferred cross-camera association

- [x] Generate candidates from topology, time and visual evidence.
- [x] Preserve raw evidence channels and allow pending/rejected outcomes.
- [x] Resolve reciprocal candidates using chronology without claiming exact XY
  position.
- [ ] Consume stable provider tracklets instead of repairing arbitrary source
  fragments inside the global resolver.
- [ ] Maintain revisable sequence hypotheses as later evidence arrives.
- [ ] Calibrate same-person/different-person likelihoods on held-out subjects
  and sessions.
- [ ] Learn travel-time distributions and overlap tolerances per installation.

Exit criterion: weak handoffs remain pending or unknown; confirmed sequences
are identity-pure on a held-out multi-camera set.

## 6. Deployment adaptation and operations

- [ ] Keep site configuration limited to streams, zones, topology, approximate
  travel windows and optional camera calibration.
- [ ] Validate on provisional layouts for generic failures, then perform a
  short acceptance test after final camera placement.
- [ ] Profile the intended camera count and configure load shedding.
- [ ] Expose reconnect, inference, evidence and confirmation metrics.
- [ ] Define retention, access, encryption and deletion policy before any
  production biometric deployment.

Exit criterion: the system meets a documented capacity and accuracy target and
degrades to observable uncertainty rather than a silent wrong identity.

## Immediate sequence

1. [x] Condense operational documentation and separate historical evidence.
2. [x] Run the complete automated suite and inspect the current checkpoint for
   generated media, local configuration and credentials.
3. [x] Commit the coherent `codex/visual-reid` checkpoint without merging it.
4. [x] Create `codex/botsort-local-tracker` from that checkpoint.
5. [x] Audit and pin maintained/permissive tracker candidates; record license
   blockers separately.
6. [x] Define the provider contract and benchmark the complete Python local
   pipelines on the existing Meetings recording.
7. [x] Evaluate BoxMOT's four ReID trackers under the demo's explicit AGPL
   allowance; retain BoT-SORT only as the least unsafe BoxMOT reference.
8. [x] Run PeopleNet + NvDCF/NvDeepSORT on the available immutable test matrix.
   NvDCF re-association is the best result but does not merit live shadow mode:
   it yields four fragments for two people and weak, nondeterministic coverage
   in mixed pose.
9. [x] Decide the next bounded provider experiment: improve detector coverage
   without regressing the negative cases. RT-DETR and PeopleNet v2 were
   screened and rejected unchanged; v2's stable human tracks justify one
   offline, source-neutral confirmation-gate experiment before stopping.
10. [x] Test whether v1 temporal confirmation can reject v2's persistent false
    tracks while retaining stable human IDs. It passed five of seven cases and
    removed the static false tracks, but failed seated and Meetings crossing
    through human fragmentation. Reject it for live shadow mode unchanged.
11. [x] Stop tuning around provisional geometry and select PeopleNet v1.1 plus
    bounded NvDCF as a conservative shadow-only tracklet provider.
12. [x] Invalidate the DeepStream live-integration handoff after confirming the
    final deployment is native Windows on two AMD RX 9070 XT cards. Preserve
    DeepStream only as benchmark evidence.
13. [x] Prepare the reproducible `experiments/windows-onnx-gpu/` hardware gate
    with CPU tests, strict provider profiling, explicit device selection,
    independent dual-process execution and JSONL output.
14. [x] Run the gate on the final AMD computer and attach its JSONL report.
    `experiments/windows-onnx-gpu/output/benchmark-20260928T115538.jsonl`
    records the MIGraphX failure, and
    `experiments/windows-onnx-gpu/output/benchmark-20260928T115558.jsonl`
    records the passing DirectML matrix. Both remain ignored local evidence.
15. [x] Benchmark YOLOX-Tiny as the portable regression baseline, YOLOX-M,
    and the separate RF-DETR Medium follow-up on the immutable recordings.
    None passed the offline negative and difficult-pose gate.
16. [ ] Validate the surviving detector with DirectML on both RX 9070 XT cards,
    separately and concurrently, without CPU fallback.
17. [ ] In a separate checkpoint, select a local tracker using the accepted
    detector; only then consider a native Frigate/go2rtc shadow worker.

## Next-session handoff

Item 15 and the RF-DETR Medium follow-up are complete with a negative selection
result. The ignored `rfdetr-medium-cpu-030.jsonl` records every sampled frame
and seven CPU provider profiles. Review its screen-person false positives and
partial-person misses when designing the next candidate or labelled eval set.
The next detector must pass interference and difficult-pose gates offline
before item 16 tests it through DirectML on both RX cards. The hardware gate
from `70ef79e` remains valid; LUID-to-PCI correlation does not block offline
work. Do not select a tracker or connect live sources yet.
