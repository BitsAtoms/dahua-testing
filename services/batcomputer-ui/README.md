# Batcomputer UI (draft)

The final PC drives nine displays in a replica Batman computer: two portrait
side screens, a 2x2 landscape block and three small console screens. This
module will show the system on all of them, one window per display, in the
owner's style. It starts with the visual style; the display inventory of the
final PC and the content of each screen are pending (roadmap).

```text
side_left    top_left     top_right     side_right
(portrait)   bottom_left  bottom_right  (portrait)
   mini_left        mini_center        mini_right
```

## Files

```text
web/theme.css         palette, contrast roles and shared components
web/style-sample.html the nine screens to scale with example content
```

Open the sample directly in Chrome; it needs no server:

```powershell
start chrome "$PWD\services\batcomputer-ui\web\style-sample.html"
```

Each screen is laid out at its native resolution and scaled into a preview of
the whole wall, so sizes are designed in real pixels. The layout follows the
owner's photo and the resolutions are provisional until the final PC's
display inventory arrives.

## Style rules (owner, 2026-10-01)

Modern Batcomputer with classic and 1990s Batman tints: black and washed black
dominate, yellow gives the contrast.

| Role | Color | Contrast |
|---|---|---|
| Main text | yellow `#FDE311` on black, washed black or dark steel | 16.0, 12.0, 10.5:1 |
| Secondary text | ochre `#988829` on black only | 5.8:1 (4.4:1 on washed black: large text only) |
| Lines, grids, inactive marks | light steel `#505c7c` | 3.1:1, never text |
| Alarms, highlights | black on yellow | 16.0:1 |
| Accents | dark steel `#282e3c` | |

- Status is never color alone: ok is an ochre dot, a warning a yellow triangle
  with an outline, an error black on yellow with a hazard edge and a slow blink,
  unknown a hollow ring with a dashed outline.
- Yellow and black hazard stripes echo the physical console.
- Faint scanlines and a soft yellow glow give the 1990s CRT tint; blinking
  stops under `prefers-reduced-motion`.
- Fonts ship with Windows 11: Bahnschrift (condensed, technical) and Cascadia
  Mono (consoles and figures). Nothing is downloaded.
- The supervisor console (owner's example for `mini_center`): black background,
  yellow monospace text only.
