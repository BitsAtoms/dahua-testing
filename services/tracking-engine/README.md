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
ended tracks plus the current input backlog.

The ignored output database is
`runtime/tracking-engine/tracking.sqlite3`. Derived records and processed
message IDs use the same seven-day retention period as the ingestion modules.

The next milestone is camera topology and temporal handoff candidates. Visual
identity is a later, independent scoring layer.
