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

Source adapters may also attach a source-neutral `track_eligibility` attribute.
`provisional` tracks remain visible and may produce explicitly provisional
candidates, while `excluded` and `contaminated` tracks cannot create handoff
candidates. If a late enrichment changes an existing track to either ineligible
state, every derived candidate touching it is removed. Raw receiver updates and
the local track remain available for audit; exclusion never deletes evidence.

Classification messages are metadata enrichments rather than lifecycle
observations. Applying one after `end` therefore preserves the source end,
geometry, zones, timestamps and prior quality instead of reopening or extending
the track.

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

When the building plan `runtime/spaces/space-map.json` exists, the live
service reloads it automatically. The plan is written by the Batcomputer space
editor (`space_map.v2`, see `services/batcomputer-ui/README.md`): each camera
counts in its room, and each door between two rooms or floor link becomes one
two-way transition with a general window of 0 to 30 s, until door times are
measured (roadmap phase 7). Doors to the exterior are not transitions. The
engine creates a handoff candidate only when an ended local track and a later
track satisfy a transition and its travel-time window. The score ranks timing within that window; it is not an
identity probability and no `global_person_id` is assigned yet.
The temporal matcher uses each transition's `overlap_tolerance_seconds`
(two seconds by default) because one source can publish its track closure just
after the next camera has already opened a track. The signed raw gap remains
stored in the candidate evidence. Two rooms joined by a transition get 8 s
instead when a camera of one sees part of the other ("vista adicional" in
the editor), because both cameras can see the same person at once. The
engine rebuilds its candidates only when cameras or transitions change, not
when a room is renamed or redrawn. The old space mapper's `space_map.v1`,
with typed directed transitions, still loads for tests and older maps.

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

## Presences and occupancy

`tracking_engine/presence.py` turns local tracks into *presences*, the unit
that occupancy counts. Sources fragment one person into several tracks: the
Dahua test camera closed and re-opened the track of a seated person every 1-6
minutes at the same image position. The rules are source-neutral and
deterministic:

- a track joins a live presence of the same camera when its first centre is
  within `join_distance` (0.15, normalized) of the presence's latest centre;
- a presence counts after `confirm_seconds` (3 s) of observed track time;
- a confirmed presence keeps counting for `hold_seconds` (20 s) after its
  last track;
- a space covered by several cameras counts the maximum over them;
- **transfer** (owner decision, 2026-10-05): when a handoff candidate's
  destination track starts a new presence and its origin track is the last
  one of a presence in another space that is no longer seen, that presence
  moves. It stops counting in its space as soon as the new presence counts,
  instead of being held there for 20 s. Each new presence takes the
  best-scored origin still free, in order of appearance. `occupancy(...,
  links=...)` applies it and reports the moves as `transfers`; without links
  nothing changes.

Replaying 26 recorded minutes of one seated person gave the right count in
93.4 % of seconds, against 78-83 % when counting raw tracks. Replaying 24 h
of the development PC's three cameras (2026-10-05, one moment every 5 s,
each seen as known then) with the transfer rule found 47 moves and 2.6 %
fewer people-moments, the ones a person was counted in two rooms at once.
A wrong guess undercounts the origin room until its camera sees the person
again. The defaults are
provisional until calibrated with group visits after final camera placement;
joining by proximity can merge two people standing very close.

Each local track stores `first_geometry_json` (schema version 6) so a new
track can be joined where it appeared. Rows created before that version fall
back to their latest geometry. The Batcomputer live map
(`services/batcomputer-ui`, `top_right`) computes occupancy, with transfers,
from the last 15 minutes of tracks on every refresh.

Visual identity remains a later, independent scoring layer.
