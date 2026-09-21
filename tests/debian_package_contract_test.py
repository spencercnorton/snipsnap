#!/usr/bin/env python3

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def job_section(document: str, name: str, next_name: str | None = None) -> str:
    start_marker = f"\n{name}:\n"
    start = document.find(start_marker)
    if start < 0:
        raise AssertionError(f"missing CI job {name}")
    start += 1
    if next_name is None:
        return document[start:]
    end = document.find(f"\n{next_name}:\n", start)
    if end < 0:
        raise AssertionError(f"missing CI job {next_name}")
    return document[start:end]


class DebianPackageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ci_path = ROOT / ".gitlab-ci.yml"
        cls.ci = ci_path.read_text(encoding="utf-8") if ci_path.is_file() else None
        cls.rules = (ROOT / "packaging" / "debian" / "rules").read_text(
            encoding="utf-8"
        )

    def require_ci(self) -> str:
        if self.ci is None:
            self.skipTest("CI configuration is not part of this tree")
        return self.ci

    def test_open_merge_request_suppresses_only_duplicate_push_pipeline(self) -> None:
        ci = self.require_ci()
        merge_request_rule = '- if: \'$CI_PIPELINE_SOURCE == "merge_request_event"\''
        duplicate_rule = (
            "- if: '$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS && "
            '$CI_PIPELINE_SOURCE == "push"\''
        )
        self.assertIn(merge_request_rule, ci)
        self.assertIn(duplicate_rule, ci)
        self.assertLess(ci.index(merge_request_rule), ci.index(duplicate_rule))
        duplicate_index = ci.index(duplicate_rule)
        self.assertIn("when: never", ci[duplicate_index : duplicate_index + 180])

    def test_published_package_ships_both_halves_of_the_bridge(self) -> None:
        """The published package is the whole product, receiver and extension.

        This used to assert the opposite -- that the extension must never
        appear in the production package -- back when the bridge was an
        experiment carried by the reference desktop's test kit. A stranger
        installing from the public archive has no kit, so a package with only
        the receiver in it is a package whose headline feature does not exist.
        """
        production = job_section(self.require_ci(), "debian_package", "publish_apt")
        for required in (
            "/usr/share/gnome-shell/extensions/$EXTENSION_UUID/extension.js",
            "schemas/gschemas.compiled",
            "gnome-shell-bridge-v1.sock",
            "snipsnap-annotator status",
        ):
            self.assertIn(required, production)
        self.assertNotIn("leaked into production", production)

    def test_published_package_carries_no_epoch(self) -> None:
        """An epoch is how this package would outrank a distribution's own.

        `snipsnap` is a separate package name, so nothing needs to be
        outranked -- and reintroducing an epoch would also silently break the
        filename the job asserts, because dpkg omits it from filenames.
        """
        production = job_section(self.require_ci(), "debian_package", "publish_apt")
        self.assertIn('test "${PACKAGE_VERSION#*:}" = "$PACKAGE_VERSION"', production)
        # And the filename must be built from the version verbatim -- stripping
        # an epoch here is what would let one back in unnoticed.
        self.assertIn('snipsnap_${PACKAGE_VERSION}_${PACKAGE_ARCH}.deb', production)

    def test_debian_rules_build_the_bridge_unconditionally(self) -> None:
        """No build knob: every caller wanted ON, so OFF was dead configuration."""
        self.assertIn("-DENABLE_GNOME_SHELL_BRIDGE=ON", self.rules)
        self.assertNotIn("SNIPSNAP_GNOME_SHELL_BRIDGE", self.rules)

    def test_ci_carries_no_bridge_build_knob(self) -> None:
        self.assertNotIn("SNIPSNAP_GNOME_SHELL_BRIDGE", self.require_ci())

    def test_debian_rules_install_the_extension_and_compile_its_schema(self) -> None:
        """Nothing else compiles that directory -- an extension schema is not
        in /usr/share/glib-2.0/schemas, so no postinst trigger will find it."""
        self.assertIn("usr/share/gnome-shell/extensions/$(EXTENSION_UUID)", self.rules)
        self.assertIn("glib-compile-schemas --strict $(EXTENSION_DEST)/schemas", self.rules)
        # The real binary is snipsnap; flameshot is the compatibility symlink
        # that keeps `Provides: flameshot` honest. Pointed the other way round
        # this is a symlink to itself, which dpkg installs without complaint.
        self.assertIn("ln -sf snipsnap debian/snipsnap/usr/bin/flameshot", self.rules)
        self.assertNotIn("ln -sf snipsnap debian/snipsnap/usr/bin/snipsnap", self.rules)


if __name__ == "__main__":
    unittest.main()
