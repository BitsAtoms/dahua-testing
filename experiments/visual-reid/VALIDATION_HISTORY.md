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

## Portable ONNX detector screen — item 15, 2026-09-28

The starting branch was clean at `9bba2f4` (parent `70ef79e`). An inventory
found seven retained immutable scenarios: Reception robot alone, person near
robot, Meetings mixed pose, seated/partially visible, empty with displays,
Meetings two-person crossing, and Reception two-person crossing. Prior local
outputs included the controlled SSD/YOLOX report and DeepStream runs. The
existing official YOLOX-Tiny ONNX was kept solely as a regression baseline.
The sole new candidate was Megvii YOLOX-M release `0.1.1rc0`, downloaded from
the official release after checking Apache-2.0 licensing, the upstream ONNX
table, export path and demo preprocessing/decoding. Exact URLs, bytes and
SHA-256 are in `onnx-detector-models.json`.

Both models ran through ONNX Runtime CPU at threshold `0.4` and 5 sampled FPS.
The ignored per-frame reports are
`output/onnx-detector/yolox-tiny-cpu-040.jsonl` and
`output/onnx-detector/yolox-m-cpu-040.jsonl`. Each contains the recording hash,
boxes, scores, frame detection/false-positive flags and inference latency.
ORT profiling showed only `CPUExecutionProvider` nodes in both runs. The
reported rates are frames with any detection, not annotated per-person recall;
the mixed clips include stretches without people.

| Recording | Frames | Tiny detections | M detections | M median CPU latency |
|---|---:|---:|---:|---:|
| Reception robot only | 151 | 0 | 128 false-positive frames | 115.1 ms |
| Reception person near robot | 151 | 128 | 128 | 115.6 ms |
| Meetings mixed pose | 153 | 79 | 86 | 116.2 ms |
| Meetings seated/partial | 148 | 148 | 148 | 116.0 ms |
| Meetings empty with displays | 150 | 19 false-positive frames | 1 false-positive frame | 115.4 ms |
| Meetings crossing | 451 | 168 | 190 | 115.8 ms |
| Reception crossing | 451 | 177 | 195 | 115.3 ms |

YOLOX-Tiny's median CPU latency was about 23–24 ms across scenarios. Visual
inspection of retained frames confirmed the YOLOX-M Reception box surrounds
the stationary robot and one Tiny empty-room box surrounds a chair. YOLOX-M's
robot detections occurred in 128/151 sampled frames, at median score `0.508`.
Filtering recorded scores retrospectively at `0.7` leaves one robot frame but
only 22/153 mixed-pose frames; this does not produce an acceptable operating
point. Its seated result is encouraging but cannot compensate for persistent
interference and weak difficult-pose coverage. The negative-scene false
positives also reject YOLOX-Tiny as an accepted detector.

Decision: item 15's offline comparison is complete, but **neither detector
passes**. Item 16 DirectML detector validation is deferred until a new
officially sourced, licensed ONNX detector clears the same offline matrix.
The prior DirectML hardware gate remains proven for its synthetic model on
both RX 9070 XT cards; no claim is made yet for these detector graphs.

## RF-DETR Medium ONNX follow-up — 2026-09-28

Started from clean `codex/directml-detector-benchmark` at `46a12e7` (parent
`9bba2f4`) and continued on `codex/rfdetr-medium-detector`. This follow-up
evaluated one model only. Upstream [RF-DETR v1.11.0](https://github.com/roboflow/rf-detr/releases/tag/v1.11.0)
designates Medium code and weights Apache-2.0. The official package listed
`rf-detr-medium.pth` at
`https://storage.googleapis.com/rfdetr/medium_coco/checkpoint_best_regular.pth`
with MD5 `7223f764a87b863f02eb8d52bf0ce2ee`. The local download matched
that MD5, measured 404,992,918 bytes and SHA-256
`749ff6071828aaffac63e204c4f4135ed3d6cdae4d702e086c360edc3b5768c8`.
The official exporter in `rfdetr==1.11.0` produced float32 ONNX opset 17,
131,569,304 bytes, SHA-256
`534ca11b273449052cf83f6d6eb53c5e11585225d41c66d271004150618f7937`.
The pinned sources and sizes are in `onnx-detector-models.json`; the export
environment is in `requirements-rfdetr-export.lock.txt`. Both weight files
are ignored local data.

The upstream ONNX preprocessing/decoding recipe uses RGB, bilinear 576 × 576
resize, `[0,1]` float32, ImageNet normalization, sigmoid logits and normalized
`cxcywh` boxes. The official COCO checkpoint retains sparse class IDs, so
person is logit slot **1**, not 0. The exported graph exposes `dets`
`[1,300,4]` and `labels` `[1,300,91]`. Our preprocessing differed from the
upstream transform by at most `1.97e-5` on a controlled synthetic image.
On the first seated/partial recording frame at threshold 0.4, official
PyTorch and ONNX both returned one person: scores `0.94509208` and
`0.94509143`, respectively; their box coordinates differed by less than one
pixel. This checks preprocessing, sparse class selection and decode against
the upstream implementation before the full replay.

The authoritative replay used ONNX Runtime `1.23.2` on CPU, threshold 0.3,
5 sampled FPS and all seven immutable recordings. The ignored
`output/onnx-detector/rfdetr-medium-cpu-030.jsonl` has 1,655 frame rows,
seven scenario summaries, recording SHA-256s, scored boxes, negative-scene
false-positive flags and measured latency. All seven separate ORT session
profiles reported only `CPUExecutionProvider` nodes. The first preliminary
0.4 run hit the ORT profiler event cap late in the replay; the runner now
creates and verifies one session per recording. Counts at 0.4 and 0.5 below
are filters of the authoritative 0.3 per-box scores.

| Recording | Frames | RF-DETR 0.3 | RF-DETR 0.4 | RF-DETR 0.5 | 0.3 median CPU latency |
|---|---:|---:|---:|---:|---:|
| Reception robot only | 151 | 0 | 0 | 0 | 192.8 ms |
| Reception person near robot | 151 | 128 | 128 | 128 | 193.6 ms |
| Meetings mixed pose | 153 | 128 | 100 | 59 | 204.8 ms |
| Meetings seated/partial | 148 | 148 | 148 | 142 | 202.0 ms |
| Meetings empty with displays | 150 | 4 false positives | 2 false positives | 0 | 200.8 ms |
| Meetings crossing | 451 | 207 | 182 | 176 | 201.6 ms |
| Reception crossing | 451 | 182 | 181 | 180 | 185.7 ms |

The robot-only result is a real gain over YOLOX-M's 128/151 false-positive
frames, and seated/partial coverage is strong. Visual review showed that the
four empty-room boxes at 0.3 surround a person *shown on the central screen*;
three occur between 28.0 and 28.8 seconds. The 0.4 screen boxes have scores
`0.484045` and `0.464464`. At 0.3 the mixed-pose clip still has 25/153
frames with no detection, including runs of six sampled frames (1.0 second)
when the real person is small and partly hidden by a chair. At 0.4 these
runs grow to ten and nine frames. Raising the threshold enough to clear the
screen boxes leaves at most 59/153 mixed-pose frames with a detection at 0.5.
Some positive clips also contain duplicate overlapping boxes, so these
frame-level hit counts must not be mistaken for clean per-person recall.
Mixed crossing clips include intervals with nobody present and have no
per-person box annotations; their hit fractions are coverage proxies only.

Decision: **RF-DETR Medium improves the baseline but does not pass the offline
detector gate.** The tested thresholds do not jointly preserve difficult-pose
coverage and reject screen people. Do not promote it to DirectML detector
validation or choose a tracker yet. The next useful evidence is frame-level
person and hard-negative annotation for the retained clips, then a separate
candidate or controlled model improvement can be measured against it. Public
data can expand pose variation, but these site-specific robot and display
negatives must remain in the local acceptance set. The prior DirectML
synthetic-model hardware result does not establish that this RF-DETR graph
executes on either RX 9070 XT.
