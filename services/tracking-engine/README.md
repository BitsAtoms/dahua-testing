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

The ignored output database is
`runtime/tracking-engine/tracking.sqlite3`. Derived records and processed
message IDs use the same seven-day retention period as the ingestion modules.

The next milestone is a continuously running projector followed by camera
topology and temporal handoff candidates. Visual identity is a later,
independent scoring layer.
