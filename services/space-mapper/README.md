# Space Mapper

Local, source-neutral editor for the physical configuration consumed by the
tracking engine. It stores no camera credentials or network addresses.

Run from the repository root:

```powershell
python services/space-mapper/server.py
```

Open `http://127.0.0.1:8091`. The server is intentionally restricted to
localhost. It discovers camera IDs already present in the durable receiver and
saves the ignored working document at:

```text
runtime/space-mapper/space-map.json
```

The editor supports:

- polygonal spaces;
- draggable camera positions;
- camera heading, field of view and approximate visual range;
- fixed or mobile camera pose;
- directed or bidirectional transitions with minimum and maximum travel time.

## Live monitor

Use `[ MONITOR ]` in the header to switch from editing to the operational 2D
view. It refreshes once per second and displays:

- active and recently received camera-local tracks;
- the logical space assigned through each camera;
- spatial/temporal handoff candidates;
- the current fused visual ranking and available modalities, when evaluated.

Markers are deliberately placed near the camera position. They do not claim
an exact floor coordinate. Candidate links are not confirmed identities, and
the UI keeps `SIN IDENTIDAD GLOBAL` visible until a separately calibrated
identity-assignment stage exists. Dahua `finalized_only` detections appear
after the camera closes its event; Frigate tracks can appear while active.

The monitor reads `runtime/tracking-engine/tracking.sqlite3` and
`runtime/visual-reid/evidence.sqlite3` without modifying either database. Its
default recent-event window is 120 seconds.

### Controlled validation sessions

Inside monitor mode, `[ INICIAR SESIÓN ]` records a bounded test interval. Use
anonymous temporary aliases such as `A` and `B`, never personal names. Enter
the expected camera route in physical order, perform the test, wait until the
final snapshots and visual scores appear, and then use
`[ FINALIZAR Y CAPTURAR ]`.

The completed report references the existing retained snapshots instead of
copying image bytes. It lets the operator assign each local track to an alias
and label each candidate pair as `Misma persona`, `Persona diferente` or
`Dudoso`. These labels are explicit test ground truth; they are not biometric
identity decisions made by the application.

Session reports and annotations are stored in the ignored local database
`runtime/space-mapper/validation.sqlite3` and removed after seven days. The
local HTTP server exposes only JPEG references located inside this repository.

All positions use normalized `0..1` map coordinates. A fixed pose can later be
used for approximate spatial projection after calibration. A mobile pose only
states which logical space currently contains the camera; its image geometry
must not be treated as a stable floor position.

`space-map.example.json` documents the committed `space_map.v1` format. The
next integration step is to load this map in the tracking engine and create
explainable temporal handoff candidates only across configured transitions.
