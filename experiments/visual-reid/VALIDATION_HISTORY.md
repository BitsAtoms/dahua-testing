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
CUDA 13.2 while Windows driver 580.97 exposes CUDA 13.0.

After updating the Windows driver, the container reported DeepStream 9.1.0,
CUDA driver/runtime 13.2, TensorRT 10.16 and cuDNN 9.23. The pinned PeopleNet
Transformer `deployable_v1.1` detector and ReIdentificationNet
`deployable_v1.0` model completed all 1,352 source frames at about 43 FPS.
Only the `Person` class was passed to tracking:

| DeepStream tracker | Person tracks | Person observations | Review |
|---|---:|---:|---|
| NvDeepSORT official config | 9 | 456 | Reject for fragmentation |
| NvDCF accuracy official config | 6 | 494 | Best integrated reference; still fragmented |
| NvDCF bounded re-association | 4 | 619 | Two clean repetitions; still fragmented |

NvDCF produced three fragments for each of the two visible people. Review at
entry, overlap, crossing and exit points found the sampled IDs identity-pure.
Unlike the YOLOX runs, it did not retain either persistent stationary
interference as a person track. The bounded variant changed only
`maxShadowTrackingAge` and `maxTrackletMatchingTimeSearchRange` from their
official values to 180 frames, plus `reidExtractionInterval` from 8 to 2. Two
clean repetitions both produced four tracks and 619 observations. No sampled
track mixed the two people. An earlier five-track/645-observation result was
invalidated after finding that the runner reused KITTI output files; the runner
now deletes only the selected scenario/tracker output directory before replay.

The same pinned detector and bounded NvDCF variant were replayed over the
available immutable scenario matrix:

| Recording | Person observations | Tracks | Result |
|---|---:|---:|---|
| Reception persistent interference only | 0 | 0 | Pass: robot not detected as a person |
| Meetings empty with displays | 0 | 0 | Pass: no false person track |
| Meetings seated/partially visible person | 368 | 1 | Pass for this bounded case |
| Reception person near interference | 290 | 2 | Robot rejected; one real person split across two separated appearances |
| Meetings mixed pose | 6-20 | 2 | Fail: weak and nondeterministic detector coverage in two clean repetitions |
| Meetings two-person crossing/occlusion | 619 | 4 | Fail: repeatable and identity-pure in review, but fragmented |

A second controlled two-person crossing was then captured from the Reception
camera at 704x576 and 25 FPS. The same pipeline produced exactly 3 tracks and
1,449 observations in two clean repetitions. Visual review showed the black
subject remaining on ID 0 throughout; the grey subject changed from ID 1 to ID
2 after becoming partially occluded/out of frame. The stationary robot was
never tracked. This is better than the four fragments from the low lateral
Meetings view, so camera geometry materially affects the result, but the
remaining split proves it is not the only cause.

The same Reception file was used to screen two official NVIDIA detector
candidates while keeping the bounded NvDCF configuration fixed:

| Detector | Person observations | Tracks | Runtime | Decision |
|---|---:|---:|---:|---|
| PeopleNet Transformer v1.1 | 1,449 | 3 | about 38.7 FPS | Safest reference: robot rejected, one human split |
| RT-DETR Warehouse EfficientViT-L2 | 2,981 | 5 | about 32.5 FPS | Reject: persistent robot tracks and a human fragment |
| PeopleNet Transformer v2.0 | 2,989 | 4 | about 16.6 FPS | Reject unchanged: two stable human IDs plus two sequential robot IDs |

PeopleNet v2 is the first tested DeepStream detector to preserve exactly two
stable human IDs through this crossing. However, IDs 0 and 3 covered the same
stationary robot before and after the people were present. Their boxes stayed
at approximately the same image coordinate and visual review confirmed the
false positive. Its high confidence means raising only the detector threshold
is not a safe remedy. The full scenario matrix was not repeated for either
candidate after this negative-case failure.

An offline, source-neutral confirmation gate then compared every PeopleNet v2
track box with independent PeopleNet v1 detections at IoU `0.3`. The robot
tracks received `0/549` and `0/822` confirmations. The two human tracks
received `653/899` and `418/719`, with maximum consecutive confirmation runs
of 145 and 57 frames. Requiring three consecutive independent confirmations
would therefore expose exactly the two stable human IDs in this recording.
This is a successful single-recording screen, not yet a validated live policy;
the full retained matrix must pass before shadow integration.

The seven-case retained matrix was then replayed with the same IoU and
three-consecutive-confirmation policy:

| Recording | Expected eligible tracks | Eligible tracks | Result |
|---|---:|---:|---|
| Reception interference only | 0 | 0 | Pass |
| Reception person near interference | 1 | 1 | Pass |
| Meetings mixed pose | 1 | 1 | Pass, but only a three-frame confirmation streak |
| Meetings seated/partial | 1 | 2 | Fail: one person remained fragmented |
| Meetings empty | 0 | 0 | Pass |
| Meetings two-person crossing | 2 | 4 | Fail: two people remained fragmented |
| Reception two-person crossing | 2 | 2 | Pass |

The policy passed five of seven cases. It reliably removed the retained static
false positives, including all robot/display tracks in the negative cases, but
did not solve human fragmentation in the difficult Meetings geometry. It is
therefore rejected as the accepted provider configuration and remains an
offline comparison only.

The Reception two-track result represents two appearances of the same real
person separated by about 13 seconds outside detection, not a robot track. A
PeopleNet threshold experiment from 0.5 to the upstream example's 0.4 did not
show an improvement and kept interference at zero. Its quantitative comparison
was invalidated by the stale-output issue, so the experiment retains the
original conservative 0.5 rather than claiming a calibrated threshold. There
is no immutable stream-loss recording yet.

## Current decision

Keep the neutral provider contract and the collectors as independent evidence
inputs. Do not promote any tested Python tracker to live use. DeepStream NvDCF
with PeopleNet v1 remains the safest integrated pipeline, but its bounded
built-in re-association still fails the local-track gate and must not enter
live shadow mode. Offline v1 confirmation reliably removed persistent false
tracks but passed only five of seven retained cases: seated and Meetings
crossing still fragmented real people. Do not promote the combined pipeline or
tune around the provisional camera geometry. Retain it as a reproducible
reference and repeat acceptance after final camera placement. Open Model Zoo remains
the best Apache-2.0 offline fragmentation reference, but its delayed merges
require an explicit track-supersession contract before live use.
