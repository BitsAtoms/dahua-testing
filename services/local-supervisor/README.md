# Local stack supervisor

This is the single entry point for the repository-owned processes. Frigate and
the MQTT broker remain external services; the Frigate adapter is managed here.

The fixed startup order is:

```text
track receiver
 -> space mapper
 -> tracking engine
 -> Dahua dashboard
 -> Frigate adapter
 -> detector-consensus shadow validator
 -> visual evidence worker
```

The detector-consensus worker is deliberately shadow-only. It validates source
person boxes against the pinned YOLOX-Tiny model and persists proposed
eligibility decisions under `runtime/visual-reid/detector-consensus.sqlite3`,
but it does not publish them or suppress tracks. Restart the supervisor after
updating to start this worker.

Copy the example only when a service needs to be disabled or a setting changed:

```powershell
Copy-Item services\local-supervisor\local-stack.example.json `
  services\local-supervisor\local-stack.json
```

The local file is ignored because deployment choices can differ per computer.
When it is absent, all services use the defaults shown in the example.

Validate local files, Python environments and imports without starting anything:

```powershell
python services\local-supervisor\run.py --check
```

The check also verifies that the dashboard ports `8090` and `8091` are free.
An occupied port normally means an individually launched copy is still active.

After stopping any individually launched copies, start the complete stack:

```powershell
python services\local-supervisor\run.py
```

Output from every child is prefixed with its service name and also appended to
`runtime/local-supervisor/logs/<session>/<service>.log`. Log sessions older
than seven days are removed. A failed child is restarted with exponential
backoff capped at 30 seconds. `Ctrl+C` requests a clean shutdown in reverse
startup order so adapters stop before their receiver.

This supervisor does not start Docker, Frigate or Mosquitto yet (roadmap
phase 4). Start them first with
`docker compose -f deploy\docker\compose.yml up -d`; Docker Desktop restarts
them afterwards. It also does not contain camera credentials;
existing ignored `.env` and `cameras.local.json` files remain authoritative.
