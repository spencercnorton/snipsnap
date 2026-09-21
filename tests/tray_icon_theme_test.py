#!/usr/bin/env python3

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAY_ICON = ROOT / "data/img/hicolor/scalable/apps/snipsnap-tray.svg"
SYMBOLIC_TRAY_ICON = (
    ROOT / "data/img/hicolor/scalable/status/snipsnap-tray-symbolic.svg"
)
TRAY_SOURCE = ROOT / "src/widgets/trayicon.cpp"
INSTALL_RULES = ROOT / "src/CMakeLists.txt"


class TrayIconThemeTest(unittest.TestCase):
    """The tray icon only reaches the panel if three things agree: the theme
    name TrayIcon asks for, the shipped file name, and the install rule."""

    def test_icon_name_matches_the_lookup_in_trayicon(self) -> None:
        lookup = re.search(
            r'QIcon::fromTheme\(\s*"([^"]+)"', TRAY_SOURCE.read_text()
        )
        self.assertIsNotNone(lookup, "TrayIcon no longer looks up a themed icon")
        self.assertEqual(lookup.group(1), SYMBOLIC_TRAY_ICON.stem)
        self.assertIn(TRAY_ICON.stem, TRAY_SOURCE.read_text())

    def test_icon_is_installed_into_hicolor(self) -> None:
        self.assertIn(
            f"share/icons/hicolor/scalable/apps/{TRAY_ICON.name}",
            INSTALL_RULES.read_text(),
        )
        self.assertIn(
            f"share/icons/hicolor/scalable/status/{SYMBOLIC_TRAY_ICON.name}",
            INSTALL_RULES.read_text(),
        )

    def test_icon_is_monochrome_on_a_16px_grid(self) -> None:
        svg = ElementTree.fromstring(SYMBOLIC_TRAY_ICON.read_bytes())
        self.assertEqual(svg.get("viewBox"), "0 0 16 16")
        text = SYMBOLIC_TRAY_ICON.read_text()
        self.assertIn("currentColor", text)
        colours = set(re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]+)"', text))
        self.assertEqual(colours, set(), "symbolic tray icon must be theme-coloured")


if __name__ == "__main__":
    unittest.main()
