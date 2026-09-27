# Where these pictures come from

Every file here is a demonstration state, never a real desktop: a headless
GNOME Shell 50 session with two virtual 1600x900 outputs, GNOME Text Editor
showing an invented release note, GNOME Calculator, and SnipSnap itself,
produced by `scripts/demo-capture.sh` in the container described by
`scripts/demo-rig/Dockerfile` and assembled by `scripts/demo-assemble.py`.

- `overlay-light.png`, `overlay-dark.png` — the capture overlay after the
  shortcut, with a selection dragged across the seam between the two outputs.
- `editor-light.png`, `editor-dark.png` — the editor window with its toolbar
  and four annotations: a rectangle, an arrow, a text label and a pixelated
  patch over the calculator display.
- `flow.png` — an animated PNG of the whole capture: shortcut, drag, Enter,
  the four annotations, Copy. The pointer is drawn in afterwards from the
  positions the rig recorded.
- `bridge-selection.gif` — the beginning of that exact recording: the idle
  desktop, <kbd>Print</kbd>, the compositor overlay, and a region dragged
  across both virtual monitors.
- `capture-to-annotate.gif` — the remainder: the committed region opens in the
  editor, receives an arrow, rectangle, text and pixelation, then is copied.

The wallpaper is "Snowy Mountain" by eberhard grossgasteiger on Pexels
(<https://www.pexels.com/photo/snowy-mountain-1287145/>), used under the
Pexels License. Every PNG is re-saved through Pillow so it carries only pixels
— no text, colour-profile or EXIF chunks; the GIFs are palette-encoded from
those same frames.
