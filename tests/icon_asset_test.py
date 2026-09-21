#!/usr/bin/env python3
"""Validate every shipped SnipSnap application-icon derivative."""

from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "data" / "img" / "app"
HICOLOR = ROOT / "data" / "img" / "hicolor"
# Windows and macOS icon derivatives are not part of every source tree.
WINDOWS_ICON = APP / "snipsnap.ico"
MACOS_ICON = ROOT / "packaging" / "macos" / "snipsnap.icns"

LEGACY_HASHES = {
    "4ffbff082997e897dfb2d92fa7eaab00629f7106f8601d08a6f9f785d6d81954",
    "7fcb6fa359065f17a798f2d1e0ce13c910b75cf033409f86435cf05ce14cf723",
    "5d1a7aaa094f958e19e7796eabdfce0788e5ef811e06a4430ba2709016fba222",
    "44461a2e1f82ea13299db13345003e522d2d2ab6917c4f51c89d74c16627ede1",
    "c060254d87e072883f87ed92ccf73820e7f49d3e07d375c107334737bed22c68",
    "c8f2104660da55e36e68d86be046c6c39b22e14ca99b1b4d21327077e789c7b5",
    "1d51cd5deda6caf1cc36284ebee788302b9950a5e4a5b47e8db6c62dfc051931",
}


def png_info(payload: bytes) -> tuple[tuple[int, int], bool]:
    """Return a PNG's dimensions and whether its IHDR declares alpha."""
    if payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height, _, colour_type = struct.unpack(">IIBB", payload[16:26])
    return (width, height), colour_type in {4, 6}


class IconAssetTest(unittest.TestCase):
    def test_vector_aliases_are_canonical_and_flame_free(self) -> None:
        vectors = [
            APP / "tech.norvi.snipsnap.svg",
            APP / "snipsnap.svg",
            HICOLOR / "scalable" / "apps" / "tech.norvi.snipsnap.svg",
            HICOLOR / "scalable" / "apps" / "snipsnap.svg",
        ]
        payloads = [path.read_bytes() for path in vectors]
        self.assertEqual(len(set(payloads)), 1)
        for path, payload in zip(vectors, payloads):
            svg = ElementTree.fromstring(payload)
            self.assertEqual(svg.get("viewBox"), "0 0 64 64", path.name)
            self.assertNotRegex(payload.decode().casefold(), r"flame|lupodharkael")

    def test_png_dimensions_alpha_and_aliases(self) -> None:
        groups = [
            (
                (128, 128),
                [APP / "snipsnap.png", APP / "tech.norvi.snipsnap.png"],
            ),
            (
                (48, 48),
                [
                    HICOLOR / "48x48" / "apps" / "snipsnap.png",
                    HICOLOR / "48x48" / "apps" / "tech.norvi.snipsnap.png",
                ],
            ),
            (
                (128, 128),
                [
                    HICOLOR / "128x128" / "apps" / "snipsnap.png",
                    HICOLOR / "128x128" / "apps" / "tech.norvi.snipsnap.png",
                ],
            ),
        ]
        for expected, paths in groups:
            payloads = [path.read_bytes() for path in paths]
            self.assertEqual(len(set(payloads)), 1, [str(path) for path in paths])
            for path in paths:
                size, has_alpha = png_info(path.read_bytes())
                self.assertEqual(size, expected, path.name)
                self.assertTrue(has_alpha, path.name)

        expected_sizes = {
            "tech.norvi.snipsnap-1024.png": (1024, 1024),
            "snipsnap.monochrome.png": (64, 64),
            "snipsnap.monochrome-1024.png": (1024, 1024),
            "snipsnap.mask.png": (36, 36),
        }
        for name, expected in expected_sizes.items():
            size, has_alpha = png_info((APP / name).read_bytes())
            self.assertEqual(size, expected, name)
            self.assertTrue(has_alpha, name)

    @unittest.skipUnless(WINDOWS_ICON.is_file(), "Windows icon is not part of this tree")
    def test_windows_icon_has_all_required_frames(self) -> None:
        with WINDOWS_ICON.open("rb") as stream:
            reserved, kind, count = struct.unpack("<HHH", stream.read(6))
            self.assertEqual((reserved, kind, count), (0, 1, 9))
            sizes = []
            for _ in range(count):
                width, height, _, _, _, bits, size, offset = struct.unpack(
                    "<BBBBHHII", stream.read(16)
                )
                sizes.append((width or 256, height or 256, bits, size, offset))
            self.assertEqual(
                [(width, height) for width, height, _, _, _ in sizes],
                [(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
            )
            self.assertTrue(all(bits == 32 for _, _, bits, _, _ in sizes))
            for width, height, _, size, offset in sizes:
                stream.seek(offset)
                frame_size, has_alpha = png_info(stream.read(size))
                self.assertEqual(frame_size, (width, height))
                self.assertTrue(has_alpha)

    @unittest.skipUnless(MACOS_ICON.is_file(), "macOS icon is not part of this tree")
    @unittest.skipUnless(
        sys.platform == "darwin" and Path("/usr/bin/iconutil").is_file(),
        "macOS iconutil only",
    )
    def test_macos_icon_has_standard_iconset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snipsnap.iconset"
            subprocess.run(
                [
                    "/usr/bin/iconutil",
                    "-c",
                    "iconset",
                    str(MACOS_ICON),
                    "-o",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            expected = {
                "icon_16x16.png": (16, 16),
                "icon_16x16@2x.png": (32, 32),
                "icon_32x32.png": (32, 32),
                "icon_32x32@2x.png": (64, 64),
                "icon_128x128.png": (128, 128),
                "icon_128x128@2x.png": (256, 256),
                "icon_256x256.png": (256, 256),
                "icon_256x256@2x.png": (512, 512),
                "icon_512x512.png": (512, 512),
                "icon_512x512@2x.png": (1024, 1024),
            }
            root = output
            self.assertEqual({path.name for path in root.glob("*.png")}, set(expected))
            for name, size in expected.items():
                frame_size, has_alpha = png_info((root / name).read_bytes())
                self.assertEqual(frame_size, size, name)
                self.assertTrue(has_alpha, name)

    def test_no_replaced_asset_matches_legacy_artwork(self) -> None:
        assets = [
            *APP.glob("snipsnap*"),
            *APP.glob("tech.norvi.snipsnap*"),
            MACOS_ICON,
            *HICOLOR.rglob("*snipsnap*"),
        ]
        offenders = []
        for path in assets:
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in LEGACY_HASHES:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_runtime_qrc_contains_every_embedded_icon(self) -> None:
        qrc = (ROOT / "data" / "graphics.qrc").read_text()
        source = (ROOT / "src" / "utils" / "globalvalues.cpp").read_text()
        ui_sources = "\n".join(
            path.read_text() for path in (ROOT / "src" / "widgets").rglob("*.ui")
        )
        runtime_paths = set(
            re.findall(r":/?(img/app/snipsnap[^\"<)]+)", source + ui_sources)
        )
        self.assertTrue(runtime_paths)
        for path in runtime_paths:
            self.assertIn(f"<file>{path}</file>", qrc, path)
            self.assertTrue((ROOT / "data" / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
