#!/usr/bin/env python3
"""Assemble the README pictures from the rig's output.

Run from the directory scripts/demo-capture.sh wrote (DEMO_OUTPUT_DIR):
overlay/editor stills are re-saved (the overlay pair scaled to 1800 px wide)
and flow/frame-*.png + flow/frames.json become flow.png, an animated PNG at
half scale with the pointer drawn in at each frame's recorded position. Two
palette-optimised GIFs make the same deterministic capture legible on README
renderers that do not animate APNGs. Review and copy the results to
docs/screenshots/.
"""
import json, pathlib, subprocess, tempfile
from PIL import Image, ImageDraw

OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)

def clean(im):
    """A fresh image with only pixels: Pillow writes no ancillary text chunks for it."""
    fresh = Image.new("RGB", im.size); fresh.paste(im.convert("RGB")); return fresh

def still(src, dst, width=None):
    im = Image.open(src)
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    clean(im).save(dst, optimize=True)
    print(dst, Image.open(dst).size, pathlib.Path(dst).stat().st_size // 1024, "KiB")

still("overlay-light.png", OUT / "overlay-light.png", 1800)
still("overlay-dark.png", OUT / "overlay-dark.png", 1800)
still("editor-crop-light.png", OUT / "editor-light.png")
still("editor-crop-dark.png", OUT / "editor-dark.png")

# The recording: half scale, a drawn pointer, per-frame delays from the rig.
frames = json.load(open("flow/frames.json"))
SCALE = 0.5
CURSOR = [(0, 0), (0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11)]  # classic arrow, 12x19

def with_cursor(im, pointer):
    x, y = pointer[0] * SCALE, pointer[1] * SCALE
    d = ImageDraw.Draw(im)
    poly = [(x + px, y + py) for px, py in CURSOR]
    d.polygon(poly, fill="white", outline="black")
    return im

imgs, durs = [], []
for f in frames:
    im = Image.open(f["file"]).convert("RGB")
    im = im.resize((round(im.width * SCALE), round(im.height * SCALE)), Image.LANCZOS)
    imgs.append(with_cursor(clean(im), f["pointer"])); durs.append(int(f["delay_ms"]))
imgs[0].save(OUT / "flow.png", save_all=True, append_images=imgs[1:], duration=durs, loop=0, optimize=True)
p = OUT / "flow.png"; print(p, Image.open(p).size, "frames", getattr(Image.open(p), "n_frames", 1), p.stat().st_size // 1024, "KiB")

# The first twelve recorded frames are the idle desktop, bridge overlay, drag,
# and settled cross-monitor selection. The remainder opens the editor, adds the
# annotations, and copies the result. Keep the clips separate: one shows why the
# GNOME bridge is different; the other shows the familiar annotation workflow.
SELECTION_LAST_FRAME = 11

def make_gif(name, first, last, width):
    """Encode a small, looped GIF from an inclusive range of the APNG frames."""
    if first < 0 or last >= len(imgs) or first > last:
        raise RuntimeError(f"invalid GIF frame range {first}..{last} for {len(imgs)} frames")
    destination = OUT / name
    with tempfile.TemporaryDirectory() as temporary:
        palette = pathlib.Path(temporary) / "palette.png"
        selection = f"select='between(n,{first},{last})',scale={width}:-2:flags=lanczos"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(p),
            "-vf", f"{selection},palettegen=max_colors=192:stats_mode=diff", str(palette),
        ], check=True)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(p),
            "-i", str(palette), "-filter_complex",
            f"[0:v]{selection}[frames];[frames][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-loop", "0", str(destination),
        ], check=True)
    print(destination, Image.open(destination).size, destination.stat().st_size // 1024, "KiB")

make_gif("bridge-selection.gif", 0, SELECTION_LAST_FRAME, 1200)
make_gif("capture-to-annotate.gif", SELECTION_LAST_FRAME + 1, len(imgs) - 1, 960)
