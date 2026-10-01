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
| `/api/screens` | screens, design canvases and which ones are built |
| `/api/console?after=<seq>` | supervisor console lines after a sequence number |

The server listens on localhost only and serves files from `web/` only.

On the development PC a screen opened at 100 % is close to its real size:
this PC's monitor has about 0.31 mm per pixel and the Batcomputer's design
canvas about 0.36 mm per CSS pixel.

## Screens

| Screen | Content | State |
|---|---|---|
| `mini_center` | supervisor console: the newest session's `supervisor.log`, followed live; credentials are masked | built, in review |
| `mini_left` | health of the system parts | pending |
| `mini_right` | health of the cameras | pending |
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

### Header icons

The yellow block of each header is decoration with an icon of what the
screen shows, without letters (owner, 2026-10-01): `web/icons.js` draws a
terminal, a pulse line, a video camera, four screens, a folded map, a photo
and a route.

## Files

```text
server.py               HTTP server (stdlib), localhost only
batcomputer_ui/         screens registry, supervisor console reader and narrator
web/theme.css           palette, contrast roles and shared components
web/screen.js           canvas fitting, clock and polling shared by the pages
web/icons.js            header icons (classic script, also used by the style sample)
web/screens/<id>.html   one page per built screen
web/pending.html        placeholder for screens not built yet and the work screens
web/preview.html        the nine screens to scale
web/style-sample.html   the approved style sample, static
displays.example.json   geometry of the final PC's displays (versioned)
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
