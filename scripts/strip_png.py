#!/usr/bin/env python3
"""Re-save every PNG under a directory from a fresh pixel copy.

Drops every ancillary chunk (tEXt/iTXt/zTXt/eXIf/iCCP/pHYs/tIME) the public
export gate refuses, and writes plain non-interlaced RGB/RGBA.
"""

import sys
from pathlib import Path

from PIL import Image

root = Path(sys.argv[1])
for path in sorted(root.rglob("*.png")):
    with Image.open(path) as source:
        mode = "RGBA" if source.mode in ("RGBA", "LA", "P") else "RGB"
        clean = Image.new(mode, source.size)
        clean.paste(source.convert(mode))
    clean.save(path, format="PNG", optimize=True)
    print(f"stripped {path.relative_to(root)} {clean.size[0]}x{clean.size[1]} {mode}")
