# Frigate replay bench

Compares Frigate object detectors on the same input, through Frigate's own
pipeline (motion, regions, filters, tracker and event lifecycle). The seven
retained recordings of `experiments/visual-reid` are looped as virtual
cameras in an isolated Frigate, one detector profile at a time.

```text
recordings (read only) -> frigate-bench (Frigate 0.17.2, no published ports)
                               -> frigate-bench-mqtt (own broker, own network)
                               -> bench.py records frigate/events
```

Nothing reaches the production Frigate, broker or track receiver.

## Files

```text
scenarios.json   the seven recordings, expected presence and tracks, detect size
profiles.json    one Frigate detector/model configuration per profile
compose.yml      isolated Frigate + Mosquitto
bench.py         writes the config, runs each profile, computes the metrics
```

The bench Frigate keeps its config, database and model copies in the ignored
`runtime/frigate-bench/`. Results go to the ignored `output/<UTC stamp>/`.

## Run

`zmq-*` profiles need the GPU client of `experiments/frigate-zmq-detector`
running (`run_detector.ps1`) and their ONNX files in that experiment's
`models/` folder. From the repository root:

```powershell
python experiments\frigate-replay-bench\bench.py
python experiments\frigate-replay-bench\bench.py --profiles openvino-mobilenet --groups recepcion --warmup 15 --loops-of-longest 0.5
```

Each camera group (`recepcion`, `reuniones`) runs separately so that one
detector does not serve seven cameras at once. The window is
`--loops-of-longest` times the longest recording in the group, after
`--warmup` seconds are discarded.

## Metrics

Person presence is rebuilt from `frigate/events` (`new`/`update`/`end`, not
false positives), the messages the Frigate adapter consumes. Per scenario:

- `person_fraction`: share of seconds with at least one person. For `absent`
  scenarios every such second is a false positive; for `present` scenarios it
  is a coverage proxy.
- `max_simultaneous`: highest number of people at the same time. Above the
  expected number it reveals a false positive or a duplicated track.
- `tracks_per_loop`: tracks divided by the number of loops, compared with
  `expected_tracks_per_loop`. Higher values mean fragmentation or false
  positives; a track can also span the loop boundary.
- Per run: the detector's median inference time as seen by Frigate and the
  share of frames Frigate skipped.

These are proxies. There is no per-second ground truth for these recordings,
and the recordings were made at provisional camera positions; they are a
regression set, not calibration data. The acceptance kit of roadmap phase 7
adds scripted scenarios with per-second counts at final positions.

Frigate's default person filters (`min_score` 0.5, `threshold` 0.7) apply to
every profile. The `zmq` request timeout is raised to 1000 ms so that quality
is not mixed with transport timeouts.

## Results, 2026-09-30 (development PC)

Run `20260930T131606Z`: warm-up 30 s, window 180 s per group (about six loops
of the 30 s recordings, two of the 90 s ones). GPU profiles ran on the RTX 3050
through `experiments/frigate-zmq-detector`; D-FINE on OpenVINO CPU because it
returns wrong results on DirectML.

| Scenario (expected) | MobileNet (OpenVINO CPU) | RF-DETR M 320 (GPU) | YOLOv9 M 320 (GPU) | D-FINE M 320 (CPU) |
|---|---|---|---|---|
| interference_only (absent) | 0 % | 0 % | 0 % | **100 %** (robot) |
| room_empty_with_displays (absent) | 0 % | 0 % | 0 % | 0 % |
| person_present_seated_partial (present) | 100 % | 100 % | 100 % | 100 %, max 2 |
| person_present_mixed_pose (present, 1 track) | **33.5 %**, 1.15 tracks | 100 %, max 3, 1.97 | 98.4 %, max 3, 2.8 | 100 %, max 3, 1.31 |
| person_near_interference (mixed, 1 track) | 100 %, max 1, 1.15 | 98.9 %, max 2, 1.15 | 100 %, max 2, 1.15 | 100 %, max 2, 0.98 |
| two_person_crossing_reception (max 2, 2 tracks) | max 3, 5.43 | max 2, 2.96 | max 2, 2.96 | max 4, 4.43 |
| two_person_crossing_meetings (max 2, 2 tracks) | max 3, 4.93 | max 3, 6.9 | max 4, 5.92 | max 4, 5.9 |

Percentages are `person_fraction`; "max" is `max_simultaneous`; the last number
is `tracks_per_loop`.

| Profile | Inference (Recepción / Reuniones) | Skipped frames (Recepción / Reuniones) |
|---|---|---|
| MobileNet | 6.6 / 5.8 ms | 0 % / 0.2 % |
| RF-DETR M | 30.5 / 26.2 ms | 8.1 % / 34.2 % |
| YOLOv9 M | 27.0 / 21.2 ms | 4.5 % / 28.5 % |
| D-FINE M | 48.5 / 46.1 ms | 31.7 % / 74.7 % |

Reading:

- MobileNet keeps both negative scenes clean but sees the person in the mixed
  pose recording only a third of the time, and adds a third person and extra
  tracks in the crossings.
- RF-DETR M and YOLOv9 M keep both negatives clean (no robot, no screen
  people at Frigate's thresholds), cover the present scenes and give the best
  Reception crossing. RF-DETR fragments less in the mixed pose scene.
- D-FINE M detects the robot as a person for the whole recording.
- One `zmq` detector on the RTX 3050 could not keep up with three or four
  cameras: frames were skipped, which inflates fragmentation. The Meetings
  columns, where skipping was highest, are the least reliable.

This is one run per profile at provisional camera positions, with proxies
instead of ground truth. The final choice is made on the final computer after
camera placement; there a detector per GPU (a second client and a second
`zmq` detector) should be measured for capacity.
