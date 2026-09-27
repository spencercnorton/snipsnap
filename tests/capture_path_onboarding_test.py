#!/usr/bin/env python3
"""Keep the bridge and portal entry points distinguishable for new users."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class CapturePathOnboardingTest(unittest.TestCase):
    def test_readme_explains_each_region_capture_path_at_first_capture(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        activation = readme.index("snipsnap-shell-bridge activate")
        guidance = readme.index("### Choose the capture path before your first capture")
        install_note = readme.index("Installing `snipsnap` replaces the Flameshot package")

        self.assertLess(activation, guidance)
        self.assertLess(guidance, install_note)
        self.assertIn("**GNOME Shell bridge**", readme)
        self.assertIn("**Desktop portal**", readme)
        self.assertIn("snipsnap-shell-bridge status", readme)
        self.assertIn("Take Screenshot (Desktop Portal)", readme)

    def test_portal_entry_points_identify_their_capture_path(self) -> None:
        tray = (ROOT / "src" / "widgets" / "trayicon.cpp").read_text(
            encoding="utf-8"
        )
        launcher = (ROOT / "src" / "widgets" / "capturelauncher.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("&Take Screenshot (Desktop Portal)", tray)
        self.assertIn("Rectangular Region (Desktop Portal)", launcher)


if __name__ == "__main__":
    unittest.main()
