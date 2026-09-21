#!/usr/bin/env python3
"""Keep every shipped product surface on the SnipSnap identity."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent


class ProductIdentityTest(unittest.TestCase):
    def test_canonical_project_urls_use_snipsnap_slug(self) -> None:
        canonical_url = "https://github.com/spencercnorton/snipsnap"
        upstream_url = "https://github.com/flameshot-org/"
        surfaces = (
            ROOT / "SECURITY.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "packaging" / "debian" / "control",
            ROOT / "packaging" / "debian" / "copyright",
            ROOT
            / "data"
            / "appdata"
            / "tech.norvi.snipsnap.metainfo.xml",
            ROOT / "data" / "man" / "man1" / "snipsnap.1",
            ROOT / "data" / "shell-completion" / "snipsnap.zsh",
            ROOT
            / "contrib"
            / "gnome-shell-extension"
            / "snipsnap-shell-bridge"
            / "metadata.json",
        )
        for surface in surfaces:
            with self.subTest(surface=surface.relative_to(ROOT)):
                if not surface.is_file():
                    self.skipTest("surface is not part of this tree")
                text = surface.read_text(encoding="utf-8")
                self.assertIn(canonical_url, text)
                # Former homes of the project are gone: a URL naming the
                # product points at the canonical repository and a URL
                # naming its upstream points at upstream, nowhere else.
                for url in re.findall(r"https?://[^\s<>\"'\\)]+", text):
                    folded = url.casefold()
                    if "snipsnap" in folded:
                        self.assertTrue(url.startswith(canonical_url), url)
                    elif "flameshot" in folded:
                        self.assertTrue(url.startswith(upstream_url), url)

    def test_qt_wayland_identity_matches_desktop_file(self) -> None:
        source = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertIn(
            'QGuiApplication::setDesktopFileName(\n'
            '          QStringLiteral("tech.norvi.snipsnap"));',
            source,
        )

        desktop = (
            ROOT
            / "data"
            / "desktopEntry"
            / "package"
            / "tech.norvi.snipsnap.desktop"
        ).read_text(encoding="utf-8")
        self.assertIn("Name=SnipSnap\n", desktop)
        self.assertIn("Exec=@LAUNCHER_EXECUTABLE@\n", desktop)
        self.assertIn("Icon=tech.norvi.snipsnap\n", desktop)
        self.assertIn("X-DBUS-ServiceName=tech.norvi.snipsnap\n", desktop)

    def test_notifications_carry_the_desktop_entry_hint(self) -> None:
        """Without this hint GNOME shows the raw app_name and, because it then
        keys the notification source on the sending pid, opens a fresh
        notification group for every capture instead of grouping them."""
        source = (
            ROOT / "src" / "utils" / "systemnotification.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'hintsMap[QStringLiteral("desktop-entry")] =\n'
            '          QStringLiteral("tech.norvi.snipsnap");',
            source,
        )

    def test_translations_contain_no_previous_product_name(self) -> None:
        forbidden = (
            "flameshot",
            "flamshot",
            "flamsehot",
            "flamesho",
            "פליימשוט",
            "فلیم",
            "火焰截图",
            "nyala api",
        )
        findings = []
        for catalog in sorted((ROOT / "data" / "translations").glob("*.ts")):
            text = catalog.read_text(encoding="utf-8").casefold()
            for token in forbidden:
                if token.casefold() in text:
                    findings.append(f"{catalog.name}: {token}")
        self.assertEqual(findings, [])

    def test_windows_version_resource_is_snipsnap(self) -> None:
        path = ROOT / "data" / "snipsnap.rc"
        if not path.is_file():
            self.skipTest("Windows version resource is not part of this tree")
        resource = path.read_text(encoding="utf-8")
        self.assertIn('VALUE "CompanyName",      "NorviTech"', resource)
        self.assertIn('VALUE "ProductName",      "SnipSnap"', resource)
        self.assertNotIn("flameshot", resource.casefold())

    def test_uncustomized_ui_uses_the_desktop_accent(self) -> None:
        source = (ROOT / "src" / "utils" / "confighandler.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("QGuiApplication::palette().color(QPalette::Highlight)", source)
        self.assertIn('m_settings.contains(QStringLiteral("uiColor"))', source)
        self.assertNotIn("{116, 0, 150}", source)

    def test_legacy_config_migration_retries_missing_files(self) -> None:
        source = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertIn("if (!legacy.exists())", source)
        self.assertIn("if (QFileInfo::exists(destination))", source)
        self.assertNotIn("!legacy.exists() || current.exists()", source)


if __name__ == "__main__":
    unittest.main()
