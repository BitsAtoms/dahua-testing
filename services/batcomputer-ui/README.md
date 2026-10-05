# Batcomputer UI

The final PC drives nine displays in a replica Batman computer: two portrait
side screens, a 2x2 landscape block and three small console screens. This
service shows the system on them, one page per display, in the owner's
style. The approved design is [`docs/diseno-batcomputer.md`](../../docs/diseno-batcomputer.md);
screens are built and reviewed one at a time on the development PC.

```text
side_left    top_left     top_right     side_right
(portrait)   bottom_left  bottom_right  (portrait)
   mini_left        mini_center        mini_right
```

## Run

The local supervisor starts it (`batcomputer_ui`, port 8092). By hand, from
the repository root:

```powershell
python services\batcomputer-ui\server.py
```

| Address | What |
|---|---|
| `http://127.0.0.1:8092/preview` | the nine screens to scale on one display, with live data |
| `http://127.0.0.1:8092/screen/<position_id>` | one screen; on the final PC each display shows one of these |
| `http://127.0.0.1:8092/editor` | the space editor, an ordinary window on a work screen |
| `/api/screens` | screens, design canvases and which ones are built |
| `/api/console?after=<seq>` | supervisor console lines after a sequence number |
| `/api/system-health` | health of the system parts (cached 2 s) |
| `/api/camera-health` | connection and last activity of each camera (cached 2 s) |
| `/api/space-map` | `GET` the building plan and its revision; `PUT {plan, revision}` saves it |

The server listens on localhost only and serves files from `web/` only.

On the development PC a screen opened at 100 % is close to its real size:
this PC's monitor has about 0.31 mm per pixel and the Batcomputer's design
canvas about 0.36 mm per CSS pixel.

## Screens

| Screen | Content | State |
|---|---|---|
| `mini_center` | supervisor console told for visitors | built, approved 2026-10-01 |
| `mini_left` | health of the system parts, and the GOTHAM row | built, approved 2026-10-01 |
| `mini_right` | health of the cameras | built, approved 2026-10-01 |
| `top_left` | video wall of every camera with the analysis drawn over it | pending |
| `top_right` | live map of the floors | pending |
| `side_left` | events of every source with thumbnails, and the Dahua collector log | pending |
| `side_right` | journeys between rooms and the day's summary | pending |
| `bottom_left`, `bottom_right` | free Windows desktops for work | not shown |

### Supervisor console (`mini_center`)

Visitors see this screen, so it tells what is really happening in plain
Spanish instead of showing the technical log (owner, 2026-10-01).
`batcomputer_ui/narrator.py` reads each line of the newest session's
`supervisor.log` (from its last 256 KB) and keeps only what relates to
something real:

| Log line | On screen |
|---|---|
| receiver `phase=new` | `◆ RECEPCIÓN · nueva persona a la vista (#4509)` |
| receiver `phase=end` | `◇ RECEPCIÓN · #4509 sale de la imagen tras 42 s` (time since its `new`) |
| receiver `phase=snapshot` | `▣ … · fotos de cuerpo y cara de #4509 guardadas` (Dahua) or `foto de … guardada` (Frigate), once per person |
| `tracking_status` | `● 2 personas en seguimiento ahora mismo`, at most once a minute; new handoff candidates as `↔ El sistema relaciona …`, at most every 15 s |
| `stack_status` | a service that stops or comes back, at once; otherwise `● Sistema en marcha: 10 de 10 piezas funcionando` every 5 min |
| start-up steps, `started`, `exited`, `ERROR` | `► Detector de IA en la tarjeta gráfica listo`, `▲ Se ha detenido …; se reinicia solo` |
| everything else (`tracking_batch`, `visual_reid_status`, detector logs…) | nothing |

On 2026-10-01, 4000 log lines in 12 minutes became 41 visitor lines. Each
line keeps its track number, so it still reads as live data. Cameras are
named after their map label, or else their room (the space mapper's map), or
else their id.

- Times are shown in local time, one row per line; older lines fade out under
  the header.
- Alerts are shown black on yellow, and periodic summaries a little dimmer.
- The header says `EN VIVO` while the raw log moves and `DETENIDO` after 30 s
  of silence (the supervisor prints its status every 10 s).
- `rtsp://user:password@` and `password=`/`token=`-style values are masked
  before anything is narrated.

### Health of the parts (`mini_left`)

`batcomputer_ui/health.py` computes it in this service, from sources it can
already read, so the rules can be tuned without restarting the supervisor:
service states from the supervisor's `stack_status` lines and restarts from
its `exited code=` lines (fed by the console reader), Frigate's `/api/stats`
and `/api/config`, a connection to the MQTT broker, and the receiver database
(read only). The rows keep a fixed order:

| Row | Ok | Problem |
|---|---|---|
| Contenedores | `en marcha` | **critical** `parados` when neither Mosquitto nor Frigate answers |
| Mensajería | `conectada` | **critical** `sin conexión` |
| Análisis de vídeo | `2 cámaras analizándose` | **error** `no responde` |
| Detector de IA · GPU/CPU | `33 ms por imagen` | **error**: `ciego` (zmq detector under 1 ms), `roto` (over 1 s), `congelado` (same value for 5 min while there are detections), `atascado` (a camera processing under half its frames, skipping over half, for 90 s: after every Frigate restart that lasts about a minute), `programa de la GPU detenido` |
| Servicios del sistema | `7 de 7 en marcha` | **critical** when the supervisor log is silent for 30 s; **error** for a stopped service; **warning** for more than 3 restarts in 10 min |
| Retraso de los datos | p95 of publication to reception over 5 min | **warning** over 2 s; `sin datos recientes` (no people) is not a fault |

The header sums up the alarms (`TODO EN ORDEN`, `1 ERROR · 1 AVISO`), in red
when one is critical. `?demo` shows example alarms to review their look.

**GOTHAM row.** Below the real rows, apart from them by a dashed line and
tagged `GOTHAM`, a seventh row shows fictional live notifications from
Batman's universe ("Ubicación del Joker desconocida", "Daño en armamento de
la Batimoto"), so a mostly healthy screen stays alive and shows every state
style (owner, 2026-10-01). Every 6 to 11 s a new one arrives with an
interference flicker, is typed letter by letter, and a thin bar drains until
the next one. They never count in the header summary. The messages, their
state and the odds of each state are in `web/data/gotham-alerts.json`: in a
label or value, `{a-b}` is a random whole number and `{x|y}` a random choice.
`tests/batcomputer-ui/test_gotham_alerts.py` checks the file after editing.

**Enigma's easter egg.** With a 1 % chance per message (about once every 15
minutes), one of Enigma's riddles appears instead, tagged `ENIGMA`, with a
`?` icon on Enigma's green `#39D353` (black on it 10.5:1), the only green of
the interface (owner, 2026-10-01). The question takes one line and the answer
the line below it; their letters shrink until both fit whole. The riddles and
the odds are in the file's `easter_egg` section; the `?enigma` address forces
them to review their look.
The supervisor will reuse these detector rules for automatic recovery
(roadmap step 4.4).

### Health of the cameras (`mini_right`)

`batcomputer_ui/cameras.py`: one row per camera, named like the console
(map label, else its room, else its id), with a `DAHUA` or `FRIGATE` tag.

| Camera | Ok | Problem |
|---|---|---|
| Frigate | `5,1 img/s · hace 2 min`; `solo vista · 10,1 img/s` without detection | **critical** `sin imagen` (`camera_fps` 0) |
| Dahua | `NetSDK + CGI · viendo personas` | **critical** `sin conexión` (NetSDK); **error** `sin eventos (CGI)` |
| either | | `Frigate no responde` / `colector sin respuesta` while its source is down (the camera stays listed) |
| switched off | `desactivada`, listed last, not counted | |

The last activity comes from the newest track update of each camera in the
receiver database; under 10 s it reads `viendo personas` with a pulsing
mark, so the screen moves with real data. The header says `4 DE 4
CONECTADAS`, in red when a camera is critical. More than six cameras use two
columns. Camera addresses from the Dahua collector never leave the module.
`?demo` shows example states.

### Building plan (`space_map.v2`)

The new space editor (roadmap step 2b) writes the building plan;
`batcomputer_ui/spaces.py` validates and stores it. Until the switch (step
2d) it is a separate file, and the tracking engine and the old space mapper
keep using `runtime/space-mapper/space-map.json` (`space_map.v1`) unchanged.

```text
workspace (one building or office)
 ├─ grid: columns x rows, shared by every floor so they draw at one scale
 ├─ floors (1 to 3, in display order)
 │   ├─ rooms: outline of grid corners, walls horizontal or vertical only
 │   ├─ cameras: room (or null), position in half grid steps, heading
 │   └─ doors: a straight stretch of wall between two rooms, or to "exterior"
 └─ floor_links: stairs or elevator joining rooms of two floors (no geometry)
```

- Every room is an exact union of grid cells, so overlaps, shared walls and
  doors are checked with integers. Rooms may share walls but never cells.
- A door between two rooms lies on the wall they share; a door to the
  exterior (a building entrance) lies on a wall that no other room touches.
- A camera sits inside its room or on its wall. `heading_deg` is 0 towards
  the top of the plan and grows clockwise.
- No travel times: until phase 7 measures each door, the tracking engine will
  use one general window (up to 30 s, as the current transitions).
- Identifiers are unique across the workspace; `exterior` is reserved.
- Validation messages are in Spanish: the editor shows them to the owner.

`space-map.example.json` is a versioned example with generic names. The real
plan names real rooms, so it is site data and stays in the ignored
`runtime/spaces/space-map.json`. Each save that changes it keeps the previous
version in `runtime/spaces/backups/` (the newest 50). A save names the
revision (SHA-256 of the file) it was edited from, so two editor windows
cannot silently overwrite each other (`409`). `PUT` is refused unless both
`Host` and `Origin` are local, so another site open in the browser cannot
write the plan.

### Space editor (`/editor`)

An ordinary window on a work screen: it fills the window instead of a fixed
canvas. Part 2b-1 (2026-10-05) edits floors and rooms; cameras, doors,
floor links and warnings come in part 2b-2. Doors, cameras and links already
in the plan are kept, except that deleting a room or a floor removes what
referred to it.

| Control | What it does |
|---|---|
| floors column | open a floor; `+ Nueva planta` up to three |
| `▭ Sala` (`S`) | drag: a rectangle; click corner by corner: any shape with straight walls (an L); click the first corner or `Intro` to close, `Retroceso` removes the last corner, `Esc` cancels |
| `↖ Seleccionar` (`V`) | click a room; drag a corner (its neighbours follow, so walls stay straight), a wall, or the inside to move it; double click or the side panel renames it; `Supr` deletes it |
| side panel | the room's name and size in grid cells; with no room selected, the floor's and the workspace's names and `Borrar planta` (asks twice when the floor has rooms) |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo (200 steps) |
| `Ctrl+S`, `Guardar` | save; `● Cambios sin guardar` until then, and closing the window asks first |

Corners snap to the grid. While drawing or dragging, the shape shows its size
in cells, or in red with a cross and the reason ("se solapa con «Oficina»")
when it cannot go there; releasing an invalid shape keeps the previous one.
The floor before the current one is drawn dashed in grey, to line up the
floors. A room's name is written in the largest rectangle inside it, so an L
keeps it in its wider part. If another window saved meanwhile, saving is
refused with a button to reload.

`web/editor/geometry.js` holds the room rules and mirrors
`batcomputer_ui/spaces.py`; `tests/batcomputer-ui/test_editor_geometry.py`
runs its Node tests (`editor_geometry.test.mjs`) and checks on the same cases
that the editor and the server accept the same rooms (skipped without Node).

### Header icons

The yellow block of each header is decoration with an icon of what the
screen shows, without letters (owner, 2026-10-01): `web/icons.js` draws a
terminal, a pulse line, a video camera, four screens, a folded map, a photo
a route, and a room with a pencil for the editor.

## Files

```text
server.py               HTTP server (stdlib), localhost only
batcomputer_ui/         screens registry, supervisor console reader, narrator, health of the parts and of the cameras, building plan
web/theme.css           palette, contrast roles and shared components
web/screen.js           canvas fitting, clock and polling shared by the pages
web/icons.js            header icons (classic script, also used by the style sample)
web/gotham.js           the GOTHAM row ticker
web/data/gotham-alerts.json  its fictional messages (edit freely)
web/screens/<id>.html   one page per built screen
web/editor/             the space editor: page, style, logic and room geometry
web/pending.html        placeholder for screens not built yet and the work screens
web/preview.html        the nine screens to scale
web/style-sample.html   the approved style sample, static
displays.example.json   geometry of the final PC's displays (versioned)
space-map.example.json  example building plan, generic names (versioned)
displays.local.json     full inventory of the final PC (ignored: device names, stable ids)
displays.local.md       the same inventory, readable (ignored)
```

## Displays of the final PC (inventory of 2026-10-01)

| Screen | Resolution | Size | Scale | Link | GPU |
|---|---|---|---|---|---|
| `side_left` | 2160×3840 (rotated 90°) | 32" | 100 % | DisplayPort | RX on PCI bus 6 |
| `top_left`, `bottom_left` | 3840×2160 | 32" | 100 %, **150 %** on `bottom_left` (primary) | DisplayPort | RX on PCI bus 6 |
| `top_right`, `bottom_right` | 3840×2160 | 32" | 100 % | DisplayPort | RX on PCI bus 3 |
| `side_right` | 2160×3840 (rotated 270°) | 32" | 100 % | DisplayPort | RX on PCI bus 3 |
| `mini_left`, `mini_right` | 1920×1080 | about 15" (EDID wrong) | 100 % | HDMI | RX of their column |
| `mini_center` | 1920×1080 | about 15" | 100 % | HDMI | Ryzen integrated GPU |

The six large screens are the same 32" 4K Samsung monitor. None is touch.
Windows numbers (`\\.\DISPLAYn`) can change after reboots or driver updates;
the inventory's `stable_id` (adapter plus connector) is the reliable key, and
positions are checked again at start.

**Design canvas.** All nine screens have almost the same pixel pitch (about
0.18 mm), so every window renders at a fixed device scale factor of 2: large
screens are designed at 1920×1080 CSS pixels, side screens at 1080×1920 and
small screens at 960×540. One CSS pixel is then about 0.36 mm on every screen,
and Windows' 150 % on `bottom_left` does not change the layout. Pages fit
their canvas into any smaller window, so the same page works in the preview.

**Mouse.** Only the two work screens are interactive. The cursor will be
confined to them by software (a low-level mouse hook), which also avoids the
14 px row offset and the gaps of the Windows layout. Planned keys:
`Ctrl+Alt+L` locks and unlocks the cursor, `Ctrl+Alt+M` brings it to the
centre of `bottom_left`.

## Style rules (owner, 2026-10-01)

Modern Batcomputer with classic and 1990s Batman tints: black and washed black
dominate, yellow gives the contrast.

| Role | Color | Contrast |
|---|---|---|
| Main text | yellow `#FDE311` on black, washed black or dark steel | 16.0, 12.0, 10.5:1 |
| Secondary text | ochre `#988829` on black only | 5.8:1 (4.4:1 on washed black: large text only) |
| Lines, grids, inactive marks | light steel `#505c7c` | 3.1:1, never text |
| Alarms, highlights | black on yellow | 16.0:1 |
| Critical or special situations only | black on red `#FF4D3A`, red on black | 6.3:1; never yellow on red (2.5:1) |
| Accents | dark steel `#282e3c` | |

- Status is never color alone: ok is an ochre dot, a warning a yellow triangle
  with an outline, an error black on yellow with a hazard edge and a slow blink,
  critical black on red with a cross and a faster blink (below 3 flashes per
  second), unknown a hollow ring with a dashed outline. For red-green
  colorblind viewers red is close to ochre (deuteranopia dE 6.0), which is why
  critical also differs in fill, icon and word.
- Yellow and black hazard stripes echo the physical console.
- Faint scanlines and a soft yellow glow give the 1990s CRT tint; blinking
  stops under `prefers-reduced-motion`.
- Fonts ship with Windows 11: Bahnschrift (condensed, technical) and Cascadia
  Mono (consoles and figures). Nothing is downloaded.
- No Batman logos for now; a bat-signal detector is a concept for later.
- The intensity of the 1990s effects is kept until it is seen on the real
  screens.
- **Privacy mode.** `Ctrl+Alt+X`, sent by a Stream Deck key, covers every
  stream of the video wall and every event photo with static ("SEÑAL
  CENSURADA"), and remembers its state across restarts. The style sample
  previews it with `X` or the `?censored` address.

Video wall notes: the Dahua cameras are not in Frigate today; their sub-streams
would be added to go2rtc for viewing only, like the door intercom. Tiles use
sub-streams so nine videos stay cheap to decode, and the boxes come from the
system's own tracks, so Dahua and Frigate cameras look the same.
