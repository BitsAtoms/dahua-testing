# Tracking engine

This service derives provider-neutral local track state from the durable
`track_update.v1` log. The receiver database remains the source of truth, so
this projection can be deleted and rebuilt without asking cameras or source
adapters to resend events.

The first milestone intentionally does not assign a persistent person identity.
It only proves these deterministic lifecycle rules:

- `new` and `update` create or refresh an active local track;
- `end` closes a live track;
- `snapshot` enriches a track without reopening it;
- a Dahua `finalized_only` snapshot creates an already-ended local track;
- duplicate messages and late updates cannot duplicate or reopen state.
- an active track with no messages for two minutes is closed with
  `end_reason=timeout`; later source activity can reopen that provisional end.

Project the current receiver log from the repository root:

```powershell
python services/tracking-engine/project_receiver.py
```

Run the incremental projector continuously:

```powershell
python services/tracking-engine/live_service.py
```

It reads committed receiver rows in small batches every 200 ms. Its cursor is
stored in the tracking database, so a restart resumes from the next receiver
row. Applying a row and advancing the cursor are separately idempotent: a
crash between them can replay a message but cannot duplicate track state.
`Ctrl+C` requests a clean shutdown. A compact status line reports active and
ended tracks, handoff candidates and the current input backlog.

When `runtime/space-mapper/space-map.json` exists, the live service reloads it
automatically. It creates a handoff candidate only when an ended local track
and a later track satisfy a configured directed/bidirectional transition and
its travel-time window. The score ranks timing within that window; it is not an
identity probability and no `global_person_id` is assigned yet.
The temporal matcher uses each transition's `overlap_tolerance_seconds`
(two seconds by default) because one source can publish its track closure just
after the next camera has already opened a track. The signed raw gap remains
stored in the candidate evidence. Configure this tolerance from Space Mapper
for transitions whose source lifecycles need a wider margin.

Inspect recent candidates without stopping the service:

```powershell
python services/tracking-engine/list_handoffs.py --limit 20 --min-score 0.5
```

Changing the map invalidates and rebuilds only candidate state. A versioned
projection reset can rebuild all local tracks from the receiver log when the
derivation rules change; source events and media are never deleted by this
operation.

The ignored output database is
`runtime/tracking-engine/tracking.sqlite3`. Derived records and processed
message IDs use the same seven-day retention period as the ingestion modules.

The next milestone is a controlled physical handoff test followed by exposing
active tracks and candidates through the local API. Visual identity is a later,
independent scoring layer.
