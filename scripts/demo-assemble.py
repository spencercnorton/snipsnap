#!/usr/bin/env python3
"""Assemble the README pictures from the rig's output: plain PNGs, no metadata chunks.

Run from the directory scripts/demo-capture.sh wrote (DEMO_OUTPUT_DIR):
overlay/editor stills are re-saved (the overlay pair scaled to 1800 px wide)
and flow/frame-*.png + flow/frames.json become flow.png, an animated PNG at
half scale with the pointer drawn in at each frame's recorded position.
Copy the results to docs/screenshots/ and update the pins in .public-release.toml.
"""
import json, pathlib
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
