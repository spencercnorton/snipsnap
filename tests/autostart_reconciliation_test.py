#!/usr/bin/env python3
"""Exercise config/autostart reconciliation through the real CLI."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(binary: str, environment: dict[str, str], enabled: bool) -> None:
    result = subprocess.run(
        [binary, "config", "--autostart", "true" if enabled else "false"],
        check=False,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"autostart command exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: autostart_reconciliation_test.py SNIPSNAP")

    binary = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="snipsnap-autostart-test.") as root:
        test_root = Path(root)
        config_home = test_root / "config"
        runtime_dir = test_root / "runtime"
        runtime_dir.mkdir(mode=0o700)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(test_root / "home"),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_CONFIG_DIRS": str(test_root / "system-config"),
                "XDG_CACHE_HOME": str(test_root / "cache"),
                "XDG_DATA_HOME": str(test_root / "data"),
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "XDG_STATE_HOME": str(test_root / "state"),
                "QT_QPA_PLATFORM": "offscreen",
            }
        )
        entry = config_home / "autostart" / "SnipSnap.desktop"

        run(binary, environment, True)
        if not entry.is_file():
            raise AssertionError("enabling autostart did not create the entry")

        entry.unlink()
        run(binary, environment, True)
        if not entry.is_file():
            raise AssertionError(
                "an unchanged startupLaunch=true value did not heal a missing entry"
            )

        entry.write_text(
            "[Desktop Entry]\nType=Application\nExec=wrong-program\n",
            encoding="utf-8",
        )
        run(binary, environment, True)
        repaired = entry.read_text(encoding="utf-8")
        if "\nTryExec=snipsnap\n" not in repaired or "\nExec=snipsnap\n" not in repaired:
            raise AssertionError(
                "an unchanged startupLaunch=true value did not repair a corrupt entry"
            )

        run(binary, environment, False)
        if entry.exists():
            raise AssertionError("disabling autostart did not remove the entry")

        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        run(binary, environment, False)
        if entry.exists():
            raise AssertionError(
                "an unchanged startupLaunch=false value did not remove a stale entry"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
