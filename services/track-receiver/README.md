# Local track receiver

This service is the provider-neutral persistence boundary for
`track_update.v1`. Dahua and Frigate keep their source-specific ingestion and
both connect here after normalization.

The first milestone is intentionally offline and transport-independent:

- validate the complete common contract;
- preserve the original normalized JSON payload;
- deduplicate idempotently by `message_id`;
- append to SQLite in WAL mode;
- remove receiver records older than seven days;
- expose simple source/camera/phase counts for diagnostics.

Replay one or more recorded streams from the repository root:

```powershell
python services/track-receiver/ingest_jsonl.py `
  path/to/dahua-track-updates.jsonl `
  path/to/frigate-track-updates.jsonl
```

The ignored database defaults to `runtime/track-receiver/receiver.sqlite3`.
The receiver stores media references, not duplicate image bytes. Source
collectors remain responsible for their own seven-day media cleanup.

Live transport and the derived per-track state are separate milestones. The
MQTT service uses QoS 1, a persistent client session, a bounded in-memory
queue, and manual acknowledgements after the SQLite commit. A broker redelivery
therefore becomes a harmless duplicate instead of a lost update. Invalid
messages are retained in a separate poison-message table and acknowledged so
they cannot create an infinite redelivery loop.

The service reuses the local Frigate broker settings when `TRACK_MQTT_HOST` is
not present:

```powershell
python services/track-receiver/mqtt_service.py
```

Publish a recorded stream through the real broker for diagnostics:

```powershell
python services/track-receiver/publish_jsonl.py path/to/track-updates.jsonl
```

The default common topic is `tracking/track-updates`. The next step is to add
durable publishers to both source adapters so they feed this transport without
removing their source-specific JSONL evidence.
