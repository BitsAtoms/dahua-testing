# Batcomputer UI (draft)

The final PC drives nine displays in a replica Batman computer: two portrait
side screens, a 2x2 landscape block and three small console screens. This
module will show the system on all of them, one window per display, in the
owner's style. It starts with the visual style; the content of the six large
screens is pending (roadmap).

```text
side_left    top_left     top_right     side_right
(portrait)   bottom_left  bottom_right  (portrait)
   mini_left        mini_center        mini_right
```

## Files

```text
web/theme.css           palette, contrast roles and shared components
web/style-sample.html   the nine screens to scale with example content
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
| `mini_left`, `mini_right` | 1920×1080 | about 15.6" (EDID wrong) | 100 % | HDMI | RX of their column |
| `mini_center` | 1920×1080 | about 15.6" | 100 % | HDMI | Ryzen integrated GPU |

The six large screens are the same 32" 4K Samsung monitor. None is touch.
Windows numbers (`\.\DISPLAYn`) can change after reboots or driver
updates; the inventory's `stable_id` (adapter plus connector) is the reliable
key, and positions are checked again at start.

**Design canvas.** All nine screens have almost the same pixel pitch (about
0.18 mm), so every window renders at a fixed device scale factor of 2: large
screens are designed at 1920×1080 CSS pixels, side screens at 1080×1920 and
small screens at 960×540. One CSS pixel is then about 0.36 mm on every screen,
and Windows' 150 % on `bottom_left` does not change the layout.

**Mouse.** `bottom_right`, `top_right` and `mini_right` sit 14 px lower than the
left column in the Windows layout, so no single `ClipCursor` rectangle keeps
the cursor out of `mini_left` and `mini_center` without cutting 14 rows of
`bottom_right`. Aligning the rows in Settings → Display fixes it; otherwise a
low-level mouse hook is needed. Gaps of 5 to 31 px also stop the cursor
between some large screens.

Open the sample directly in Chrome; it needs no server:

```powershell
start chrome "$PWD\services\batcomputer-ui\web\style-sample.html"
```

Each screen is laid out at its design canvas and scaled into a preview of the
whole wall. Sizes come from the inventory and the arrangement from the
owner's photo.

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

## Screens decided (owner, 2026-10-01)

| Screen | Content |
|---|---|
| `mini_left` | health of the system parts (Docker, MQTT, Frigate and its detector, services, transport delay) |
| `mini_center` | supervisor console: black background, yellow monospace text only |
| `mini_right` | health of the cameras: whether each one is connected |

The three small screens only show information: they are not interactive and
the mouse should not reach them. On the final PC's Windows layout the six
large screens form one block and the small ones sit below it, so the cursor
can be confined to that block with `ClipCursor` (to confirm with the display
inventory), and their windows ignore clicks. The large screens' content is
still to be decided; the sample shows examples.
