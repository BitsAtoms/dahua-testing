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

All positions use normalized `0..1` map coordinates. A fixed pose can later be
used for approximate spatial projection after calibration. A mobile pose only
states which logical space currently contains the camera; its image geometry
must not be treated as a stable floor position.

`space-map.example.json` documents the committed `space_map.v1` format. The
next integration step is to load this map in the tracking engine and create
explainable temporal handoff candidates only across configured transitions.
