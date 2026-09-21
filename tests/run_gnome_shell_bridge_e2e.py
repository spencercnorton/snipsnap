#!/usr/bin/env python3
"""Run gnome-shell-test-tool inside GNOME's mocked D-Bus environment."""

from __future__ import annotations

import os
import pathlib
import sys


def _required_directory(name: str, default: str | None = None) -> pathlib.Path:
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"{name} is required")
    directory = pathlib.Path(value).resolve()
    if not directory.is_dir():
        raise SystemExit(f"{name} is not a directory: {directory}")
    return directory


def main() -> int:
    shell_source = _required_directory("GNOME_SHELL_TEST_SOURCE")
    mutter_tests = _required_directory(
        "MUTTER_TEST_RUNNER_DIR", "/usr/share/mutter-18/tests"
    )
    shell_tests = shell_source / "tests"
    if not (shell_tests / "gnomeshell_dbusrunner.py").is_file():
        raise SystemExit(f"GNOME Shell test runner is missing under {shell_tests}")

    sys.path[:0] = [str(shell_tests), str(mutter_tests)]
    from gnomeshell_dbusrunner import GnomeShellDBusRunner
    from mutter_dbusrunner import meta_run

    return int(meta_run(GnomeShellDBusRunner))


if __name__ == "__main__":
    raise SystemExit(main())
