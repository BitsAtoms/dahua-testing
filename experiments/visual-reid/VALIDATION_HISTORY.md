# Visual tracking validation history

This file preserves decision evidence that is no longer needed in the active
roadmap. Raw captures, rendered frames, databases and reports remain ignored
runtime artifacts and are not committed.

## Evidence acquisition

| Checkpoint | Result | Decision |
|---|---|---|
| Dahua/Frigate normalization | Source lifecycle, IDs and media retained | Keep source adapters independent |
| Adaptive RTSP capture | Bounded buffers and evidence reinforcement worked on three streams | Reuse this frame boundary for local tracking |
| Face validity v3 | Rejected retained wall/sofa negatives and retained visually confirmed faces | Accept as current face gate |
| Body alignment v2 | Live `new/update` crops contained the subject; terminal crops were unsafe | RTSP crops are live-only |
| Co-visibility | Valid Dahua/Reception overlap observed in both directions | Overlap is not automatically a completed handoff |

## Cross-camera evidence

Controlled sessions showed that face evidence can help rank handoffs, but raw
similarity ranges vary substantially by subject, pose and crop quality. Body
ReID was weak for the overhead Reception view. No universal cosine threshold
was selected. The resolver therefore keeps visual channels separate and allows
pending decisions.

Frigate and Dahua repeatedly split one physical route into multiple local IDs.
One Reception source ID also survived from a stationary robot into a real
person. This established that source IDs cannot be repaired reliably only at
the cross-camera layer.

## False-positive detector checkpoint

The controlled detector set contained 314 positive and 301 negative sampled
frames. SSD threshold `0.5` plus YOLOX-Tiny threshold `0.4` with IoU `0.3`
produced `93.31%` positive-frame coverage and zero negative-frame detections at
about `30 ms` median CPU latency.

In live shadow mode, temporal hysteresis excluded the persistent Reception
robot while a nearby real person reached eligibility. Meetings seated/shadow
tests showed fragmentation and transient coverage. The result validates the
shadow guard, not production suppression.

## External tracker comparison

### Norfair

Norfair `2.2.0` with pure IoU passed the small one-person set, but produced
6--9 tracks for two people crossing in Meetings. A naive body-ReID callback
produced 8--9 tracks and mixed-person clusters. Norfair was rejected as the
live local tracker.

### Open Model Zoo

The official multi-camera tracker with
`person-reidentification-retail-0287`, replayed at 5 FPS, produced three tracks
for two people. Visual review found no identity switch: one subject was stable
and the other fragmented once. Latency was about `5 ms` on CPU.

Raising its same-camera merge threshold from `0.15` to `0.25` made the final
history unsafe/incomplete, so the adjustment was rejected. The upstream demo
also raises a cleanup `AttributeError` after valid output is saved in
precomputed-detection mode.

### Detector limitation in the same capture

The 90-second Meetings capture
`20260922T094309Z-two_person_crossing_occlusion` had 174 active sampled frames.
At least one person was detected in `88.5%`, but both people were detected in
only `25.9%`. This means the sparse consensus export is not an appropriate
input for judging a tracker whose association strategy depends on weaker
per-frame detections.

### Replaceable local-tracker providers

The full YOLOX-Tiny pipeline was replayed at 10 FPS over 676 sampled frames of
the same Meetings capture:

| Provider | Result | Median tracker latency | Decision |
|---|---:|---:|---|
| Roboflow BoT-SORT 2.6.0 | 12 tracks | 0.44 ms | Reject as motion-only solution |
| Deep SORT Realtime + OpenVINO 0287, strict | 17 tracks | 0.17 ms | Reject |
| Deep SORT Realtime + OpenVINO 0287, relaxed | 10 tracks | 0.16 ms | Reject |
| BoxMOT BoT-SORT 25.0.0 + OSNet | 7 tracks | 41.50 ms | Best BoxMOT result, but reject for fragmentation |
| BoxMOT Deep OC-SORT 25.0.0 + OSNet | 12 tracks | 38.62 ms | Reject |
| BoxMOT StrongSORT 25.0.0 + OSNet | 23 tracks | 44.42 ms | Reject |
| BoxMOT OccluBoost 25.0.0 + OSNet, upstream association | 4 tracks | 44.93 ms | Reject: unsafe identity merge |
| BoxMOT OccluBoost 25.0.0 + OSNet, conservative appearance | 5 tracks | 47.11 ms | Reject: unsafe identity merge persists |

The relaxed Deep SORT run kept the two principal human IDs distinct in the
reviewed crossing, but partial/full-body duplicate detections and intermittent
false detections still created extra IDs. Relaxing association further would
trade fragmentation for unsafe identity merges.

The project owner authorized BoxMOT's AGPL-3.0 code for this internal,
non-commercial showroom demo. It was integrated behind the neutral provider
contract and tested on CPU with `osnet_x0_25_msmt17.pt`. The smaller
OccluBoost counts were misleading: visual and temporal review showed a track
starting on a person and ending on the persistent extreme-right false
positive. Its conservative appearance thresholds did not prevent the merge.

Docker Desktop successfully exposed the RTX 3050 to an Ubuntu 24.04 CUDA 13
container. This makes an isolated DeepStream 9.1 PeopleNet +
NvDCF/NvDeepSORT benchmark feasible. The official image was subsequently
downloaded, but startup correctly rejected the host driver: the image declares
CUDA 13.2 while Windows driver 580.97 exposes CUDA 13.0. The benchmark remains
blocked until the host driver is updated.

## Current decision

Keep the neutral provider contract and the collectors as independent evidence
inputs. Do not promote any tested Python tracker to live use. BoxMOT BoT-SORT
is the least unsafe BoxMOT reference, not a passing provider. Benchmark the
integrated DeepStream detector/tracker pipeline next. Open Model Zoo remains
the best Apache-2.0 offline reference, but its delayed merges require an
explicit track-supersession contract before live use.
