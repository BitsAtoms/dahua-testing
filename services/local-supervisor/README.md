# Local stack supervisor

This is the single entry point for the whole local system: the Docker part
(Docker Desktop, Mosquitto, Frigate), the Frigate GPU detector client and the
repository-owned processes.

The fixed startup order is:

```text
Docker Desktop            started with `docker desktop start` if the engine is down
 -> Mosquitto             `docker compose up -d mqtt`, then wait for 127.0.0.1:1883
 -> Frigate GPU detector  only with "frigate_detector": "gpu"; wait for 127.0.0.1:5555
 -> Frigate               `docker compose up -d frigate`, then wait for its API
 -> track receiver
 -> space mapper
 -> tracking engine
 -> Dahua dashboard
 -> Frigate adapter
 -> detector-consensus shadow validator
 -> visual evidence worker
```

Every infrastructure step waits until its piece answers. A step that does not
become healthy in time (Docker Desktop: `docker_start_timeout_seconds`,
default 240 s; Mosquitto and the detector: 60 s; Frigate: 180 s), or a Docker
command that fails, stops the startup with `ERROR stack_startup_failed` and
exit code 3.

## Frigate and the GPU detector

Frigate's `zmq` detector checks its model only when Frigate starts. If
Frigate starts before the detector client, it stays blind (every detection
returns zero objects); if the client restarts while Frigate runs, Frigate's
cameras stall. Both were observed on 2026-09-30
(`experiments/frigate-zmq-detector/README.md`). Therefore, in GPU mode:

- Frigate is restarted once the client listens if it was already running,
  which is the normal case at boot because Docker starts it with the engine
  (`restart: unless-stopped`).
- When the client exits, it is restarted like any other child, and Frigate is
  restarted after it listens again. A failed Frigate restart is retried every
  30 s.

After Frigate answers, the supervisor compares Frigate's detector with
`frigate_detector` and logs `ERROR frigate_detector_mismatch` when they
differ: Frigate on `zmq` without the client is blind, and on the CPU the
client would idle. The client uses DirectML only, so a GPU failure is loud
instead of a silent CPU fallback; on a computer with two GPUs it uses adapter
0 (choosing a card is an open point for the final PC).

## Configuration

Copy the example only when a service needs to be disabled or a setting changed:

```powershell
Copy-Item services\local-supervisor\local-stack.example.json `
  services\local-supervisor\local-stack.json
```

The local file is ignored because deployment choices can differ per computer.
When it is absent, all services use the defaults shown in the example:
`"docker_stack": true` and `"frigate_detector": "cpu"`, matching the Frigate
template's OpenVINO detector. A computer whose Frigate uses the `zmq`
detector sets `"frigate_detector": "gpu"`. `"docker_stack": false` leaves
Docker, Mosquitto and Frigate to be started by hand; GPU mode requires the
Docker stack.

## Run

Validate local files, Python environments, imports and the Docker CLI without
starting anything:

```powershell
python services\local-supervisor\run.py --check
```

The check also verifies that ports `8090`, `8091` and, in GPU mode, `5555` are
free. An occupied port normally means an individually launched copy is still
active.

After stopping any individually launched copies, start the complete system:

```powershell
python services\local-supervisor\run.py
```

Output from every child is prefixed with its service name and also appended to
`runtime/local-supervisor/logs/<session>/<service>.log`; infrastructure steps
are prefixed with `[infrastructure]`. Everything the supervisor prints is also
written, timestamped, to `<session>/supervisor.log`, so an unattended start can
be diagnosed afterwards. Log sessions older than seven days are removed. While
it runs, the supervisor asks Windows not to sleep (`power keep_awake=on`); it
changes no power setting and does not keep the display on. A failed child is
restarted with exponential backoff capped at 30 seconds. The status line every 10 s includes `mqtt`, `frigate` and, in GPU
mode, `frigate_gpu_detector`.

`Ctrl+C` or `Ctrl+Break` requests a clean shutdown in reverse startup order:
the repository services (adapters before their receiver), then
`docker compose stop frigate`, then the detector client. Mosquitto and Docker
Desktop keep running (owner decision 2026-09-30). A stopped Frigate stays
stopped until the supervisor starts it again.

## Start at sign-in

`autostart.py` is the entry point of the Windows logon task: it runs this
supervisor, retries a failed start every 30 s and opens the map window. Its
installation and keys are described in `deploy/windows/README.md`.

## Live validation (development PC, 2026-09-30)

RTX 3050, Docker Desktop 29.4.3, Frigate 0.17.2 with RF-DETR Medium on the
`zmq` detector:

| Scenario | Result |
|---|---|
| Frigate running, client down, supervisor started | client listening after 0.5 s, Frigate restarted, API after 6 s, detector `zmq` as expected; 25 ms per inference, no `Model not ready` |
| Client killed while running | client restarted after 1 s, Frigate restarted after it listened (7 s); no skipped frames after about one minute |
| `Ctrl+Break` | all services stopped with code 0, Frigate stopped, client stopped, Mosquitto still up, no Python process left |
| Docker Desktop stopped (`docker desktop stop`), Frigate left running before | Docker Desktop answered after about 7 s, Docker started Frigate before the client, the supervisor restarted it; all services started within about 25 s; 21 ms per inference |

A start after a real reboot, with the WSL virtual machine cold, is measured
with the automatic sign-in (roadmap step 4.3).

The supervisor does not contain camera credentials; the ignored `.env`,
`deploy/docker/.env` and `cameras.local.json` files remain authoritative.
