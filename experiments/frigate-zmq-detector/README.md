# Frigate object detection on a GPU outside Docker

Docker on Windows cannot give Frigate access to AMD GPUs; Frigate's `-rocm`
image needs Linux `/dev/kfd`. Frigate 0.17 can instead send each detection
tensor to an external process through its `zmq` detector type. This
experiment runs that external process natively on Windows with ONNX Runtime
and DirectML, so Frigate keeps decoding, motion, tracking, clips and its UI in
Docker while only object detection moves to a GPU.

```text
Frigate (Docker)  --REQ tensor-->  zmq_onnx_client.py (Windows, 127.0.0.1:5555)
                  <--REP boxes---  ONNX Runtime + DmlExecutionProvider -> GPU
```

The external process is the ONNX Runtime client published by the Frigate
organization for Apple Silicon
([frigate-nvr/apple-silicon-detector](https://github.com/frigate-nvr/apple-silicon-detector),
MIT). It is generic and used unmodified; only its `--providers` argument
changes. Frigate documents it for macOS only, so on Windows it is an
evaluation, not a supported configuration.

## Files

```text
export/*.Dockerfile        pinned YOLOv9 and D-FINE export recipes
requirements.txt           pinned Python 3.12 dependencies (onnxruntime-directml)
fetch_client.py            downloads the client at a pinned commit, checks SHA-256
export_rfdetr_frigate.py   RF-DETR Medium 320x320 with normalization in the graph
verify_model.py            fails unless every node runs on the requested provider
run_detector.ps1           starts the client on tcp://127.0.0.1:5555
zmq_roundtrip.py           measures Frigate-style request round trips
```

`.venv/`, `vendor/`, `models/` and `output/` are ignored.

## Setup

From the repository root:

```powershell
py -3.12 -m venv experiments\frigate-zmq-detector\.venv
experiments\frigate-zmq-detector\.venv\Scripts\python.exe -m pip install -r experiments\frigate-zmq-detector\requirements.txt
experiments\frigate-zmq-detector\.venv\Scripts\python.exe experiments\frigate-zmq-detector\fetch_client.py
```

Export the model with the pinned RF-DETR export environment and checkpoint of
the visual-reid experiment (see `experiments/visual-reid/export_rfdetr_medium.py`):

```powershell
experiments\visual-reid\.venv-rfdetr-export\Scripts\python.exe experiments\frigate-zmq-detector\export_rfdetr_frigate.py
```

Frigate's float models receive RGB pixels divided by 255 with no mean/std
normalization, but the RF-DETR ONNX export expects ImageNet-normalized input.
The export therefore prepends `Sub(mean)` and `Div(std)` to the graph and
checks the result against the unwrapped export fed with normalized input.
Frigate's documented export command uses the same `rfdetr` exporter, so it
probably has the same gap; that was not verified here.

Verify that the model executes on the GPU:

```powershell
experiments\frigate-zmq-detector\.venv\Scripts\python.exe experiments\frigate-zmq-detector\verify_model.py experiments\frigate-zmq-detector\models\rfdetr-medium-320-frigate.onnx --provider DmlExecutionProvider --device-id 0
```

## Run

Start the client before Frigate, then copy the model into Frigate's
`config/model_cache` folder and configure Frigate:

```powershell
powershell -ExecutionPolicy Bypass -File experiments\frigate-zmq-detector\run_detector.ps1
```

```yaml
detectors:
  gpu:
    type: zmq
    endpoint: tcp://host.docker.internal:5555
    request_timeout_ms: 200

model:
  model_type: rfdetr
  width: 320
  height: 320
  input_tensor: nchw
  input_dtype: float
  path: /config/model_cache/rfdetr-medium-320-frigate.onnx
```

The client listens on loopback only. Docker Desktop forwards
`host.docker.internal` to the Windows loopback, verified on 2026-09-30.
Frigate transfers the model over ZMQ on start; the client keeps a copy in
`vendor/models/`.

## Failure behavior to design around

A request that is not answered within `request_timeout_ms` returns zero
detections (Frigate 0.17.2 `frigate/detectors/plugins/zmq_ipc.py`).

Observed on 2026-09-30 by stopping the client while Frigate was running:

1. Within about 20 s all detections dropped to zero. `zmq_ipc` logged
   `ERROR` lines, and Frigate's watchdog logged `Detection appears to be
   stuck` and restarted the detection process twice. The detector's
   `inference_speed` in `/api/stats` jumped to a meaningless value.
2. After the client was restarted, the new detection process loaded the model
   (`Model ... is ready`) and ran for a few seconds, but camera processing then
   stalled: `process_fps` 0.1 with `skipped_fps` equal to the camera frame
   rate, no motion activity and no events, for more than 30 minutes.
3. `docker restart frigate`, with the client already running, recovered fully
   within a minute; a person in Recepción was detected at score 0.985.

Not tested: Frigate starting while the client is down.

Consequences for the single startup (roadmap phase 4):

- Start the client before Frigate.
- If the client restarts, restart Frigate after it.
- Health signals: `process_fps` far below `camera_fps` with `skipped_fps`
  close to the camera frame rate, and a detector `inference_speed` that is
  absurd or no longer changes.

OpenVINO on CPU remains the fallback.

## Results on the development PC

Development PC: RTX 3050 8 GB, driver 596.36, ONNX Runtime 1.24.4. These are
mechanism results, not AMD results; the RX 9070 XT must be measured on the
final PC.

`verify_model.py` on 2026-09-30 with `rfdetr-medium-320-frigate.onnx`
(119380230 bytes, SHA-256 `5e7b1523...4e4759d`):

| Provider | Node placement | p50 | p95 |
|---|---|---:|---:|
| `DmlExecutionProvider` | all nodes on DirectML, no CPU fallback | 13.4 ms | 15.0 ms |
| `CPUExecutionProvider` | CPU | 87.9 ms | 128.6 ms |

The DirectML output differs from the CPU output by at most 4.9e-4.

Frigate using the client (five minutes, same sampler as the CPU detectors in
`deploy/docker/README.md`; about 9 detections per second, lower activity than
the CPU runs):

| Detector | Frigate inference | Detector CPU | Client CPU | Frigate container CPU |
|---|---:|---:|---:|---:|
| TFLite CPU (MobileNet) | 11.3 ms | 41 % | — | 75 % |
| OpenVINO CPU (MobileNet) | 6.1 ms | 104 % | — | 143 % |
| ZMQ + DirectML (RF-DETR Medium 320) | 36.8 ms | 2.6 % | 3.4 % | 35 % |

CPU is a percentage of one core. The client's resident memory was about
540 MB. Person events were created with scores between 0.79 and 0.99.

Round trip of one 1.2 MB float32 request (`zmq_roundtrip.py`, p50): 16.7 ms
from Windows and 24.6 ms from inside the Frigate container, against 13.4 ms of
pure inference. Docker-to-Windows transport therefore adds about 8 ms, and the
client about 3 ms; Frigate's own measurement adds its preprocessing and queue.

At about 37 ms per request one detector serves about 27 requests per second.
Right after a Frigate restart two active cameras asked for 18-23 per second
and a few frames were skipped. For more cameras: a second client on another
port (possibly on the other GPU) configured as a second `zmq` detector, and
sending `uint8` instead of `float32` (with the /255 moved into the graph)
would cut the transfer by four.

## More models: YOLOv9 and D-FINE

`export/yolov9.Dockerfile` and `export/dfine.Dockerfile` are Frigate 0.17's
documented export recipes pinned to upstream commits. The build context is
the small `export/` folder:

```powershell
docker build experiments/frigate-zmq-detector/export -f experiments/frigate-zmq-detector/export/yolov9.Dockerfile --build-arg MODEL_SIZE=m --build-arg IMG_SIZE=320 --output experiments/frigate-zmq-detector/models
docker build experiments/frigate-zmq-detector/export -f experiments/frigate-zmq-detector/export/dfine.Dockerfile --build-arg MODEL_SIZE=m --build-arg IMG_SIZE=320 --output experiments/frigate-zmq-detector/models
```

Two changes to the documented recipes were needed on 2026-09-30:

- **torch pinned to 2.8.0 (CPU wheels).** Unpinned, the recipes installed
  torch 2.14, whose dynamo exporter forces ONNX opset 18 ("requested
  opset_version 12 is lower"). DirectML in ONNX Runtime 1.24.4 then fails to
  create the session with `80070057 The parameter is incorrect`
  (`MLOperatorAuthorImpl.cpp(2853)`), and ONNX Runtime silently falls back to
  the CPU. torch 2.8.0 keeps the classic exporter: opset 12 for YOLOv9 and 16
  for D-FINE.
- **D-FINE at 320.** The recipe hard-codes 640. The model precomputes its
  positional embeddings and decoder anchors for `eval_spatial_size`, so the
  recipe also sets that size and keeps the anchor grid and valid mask the
  model generated instead of the 640 ones stored in the checkpoint (they are
  not learned).

Weight checksums printed by the builds: `yolov9-m-converted.pt`
`4f60eef3...a2349`, `dfine_m_obj2coco.pth` `183caeb1...3ec1d8`.

On Windows the DirectML error text is localized (Spanish here) and ONNX
Runtime's Python binding cannot decode it, which hides it behind a UTF-8
error. Calling `SetThreadUILanguage(0x0409)` through `ctypes` before creating
the session shows it in English.

`verify_model.py` results on the RTX 3050 (ONNX Runtime 1.24.4):

| Model (320x320) | DirectML placement | p50 | Matches CPU |
|---|---|---:|---|
| RF-DETR Medium (normalization prepended) | all nodes | 13.4 ms | yes (4.9e-4) |
| YOLOv9-M (opset 12) | all nodes | 9.5 ms | yes (7.2e-4; same scores on a real frame) |
| D-FINE-M (opset 16) | shape ops on CPU | 21.2 ms | **no**: no detections on a real frame where the CPU found three objects |

D-FINE on DirectML is therefore not usable; Frigate's documentation also
states that D-FINE currently only runs on OpenVINO CPU. It is compared on
OpenVINO CPU instead, and MIGraphX should be tried on the final AMD computer.

## Open points

- The client passes no provider options, so DirectML uses adapter 0. Choosing
  one of the two RX cards on the final PC needs a device option.
- Frigate and the client score RF-DETR with a softmax over classes, while the
  official decoding (used by the visual-reid benchmark) applies a sigmoid per
  class. Scores and thresholds are therefore not comparable with that
  benchmark; detection quality must be measured through Frigate itself.
