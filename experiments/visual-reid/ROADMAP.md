# Visual tracking roadmap

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

Current branch: `codex/visual-reid`.

Current decision: stop designing another frame-to-frame tracker. Evaluate
BoT-SORT-ReID as a replaceable local-tracker provider using its intended
per-frame detector input. Dahua and Frigate events remain source evidence and
audit inputs; they do not replace frame-level tracking.

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

## 2. Local tracker provider — current phase

- [ ] Record the exact BoT-SORT repository commit, transitive model licenses
  and reproducible isolated environment.
- [ ] Define source-neutral `DetectionFrame`, `LocalTrackUpdate` and
  `LocalTrackerProvider` contracts.
- [ ] Run detector + BoT-SORT-ReID over the immutable controlled recordings at
  realistic cadence; do not reuse only the sparse consensus boxes.
- [ ] Measure track count, fragmentation, ID switches, false tracks, detector
  coverage, latency, CPU/GPU and memory.
- [ ] Pass the Meetings two-person gate: exactly two identity-pure tracks with
  no observed switch.
- [ ] Pass empty, interference, seated, one-person and stream-loss cases.
- [ ] Decide whether BoT-SORT is accepted, rejected or requires a different
  maintained implementation behind the same contract.

Exit criterion: one pinned pipeline passes the local test matrix on immutable
recordings and has a clear license/deployment path. Benchmark thresholds are
not presented as universal production calibration.

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
4. [ ] Create `codex/botsort-local-tracker` from that checkpoint.
5. [ ] Pin and reproduce the official MIT BoT-SORT baseline in an isolated
   environment.
6. [ ] Define the provider contract and benchmark the complete detector/tracker
   pipeline on the existing recordings.
7. [ ] Decide from the exit criterion whether to proceed to live shadow mode.
