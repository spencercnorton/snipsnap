#!/usr/bin/env python3
"""Contract tests for the narrowly gated GNOME startup retry."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tests" / "run_gnome_shell_e2e_with_startup_retry.py"
CI_PATH = ROOT / ".gitlab-ci.yml"
CI = CI_PATH.read_text(encoding="utf-8") if CI_PATH.is_file() else None


FAKE_COMMAND = """\
import os
import pathlib
import sys

state = pathlib.Path(sys.argv[1])
modes = sys.argv[2].split(",")
attempt = int(state.read_text()) + 1 if state.exists() else 1
state.write_text(str(attempt))
mode = modes[min(attempt - 1, len(modes) - 1)]
output = pathlib.Path(os.environ["GNOME_SHELL_E2E_OUTPUT_DIR"])
(output / "attempt-artifact.txt").write_text(f"attempt={attempt}\\n")
js_marker = (
    f"SNIPSNAP-E2E-JS-START script={os.environ['SNIPSNAP_E2E_SCRIPT']} "
    f"attempt={os.environ['SNIPSNAP_E2E_ATTEMPT']}"
)
if mode in {"startup-timeout", "success"}:
    print("GNOME Shell started at 123")
if mode == "js-timeout":
    print(js_marker)
if mode == "success":
    print(js_marker)
    raise SystemExit(0)
if mode in {"pre-startup-timeout", "startup-timeout", "js-timeout"}:
    raise SystemExit(124)
raise SystemExit(7)
"""


class GnomeShellE2EStartupRetryTest(unittest.TestCase):
    def run_modes(
        self, modes: str
    ) -> tuple[subprocess.CompletedProcess[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake = root / "fake.py"
        fake.write_text(textwrap.dedent(FAKE_COMMAND), encoding="utf-8")
        state = root / "count"
        output = root / "artifacts"
        result = subprocess.run(
            [
                sys.executable,
                str(WRAPPER),
                "--output-dir",
                str(output),
                "--",
                sys.executable,
                str(fake),
                str(state),
                modes,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        decision = json.loads((output / "startup-retry.json").read_text())
        return result, output, decision

    def test_ci_wraps_both_bounded_harnesses(self) -> None:
        if CI is None:
            self.skipTest("CI configuration is not part of this tree")
        invocation = (
            "python3 tests/run_gnome_shell_e2e_with_startup_retry.py\n"
            "      --output-dir"
        )
        self.assertEqual(CI.count(invocation), 2)
        self.assertEqual(
            CI.count("timeout --signal=TERM --kill-after=30s 2m"), 2
        )
        for script in (
            "tests/gnome_shell_bridge_e2e.js",
            "tests/gnome_shell_bridge_mixed_e2e.js",
        ):
            self.assertIn(script, CI)
        self.assertIn(
            "node --check --input-type=module < "
            "tests/gnome_shell_bridge_mixed_e2e_impl.js",
            CI,
        )
        self.assertIn(
            "node --test tests/gnome_shell_bridge_mixed_entry.test.mjs", CI
        )

    def test_retries_timeout_only_before_shell_and_js_start(self) -> None:
        result, output, decision = self.run_modes("pre-startup-timeout,success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(decision["attempts"]), 2)
        self.assertTrue(decision["attempts"][0]["retry"])
        self.assertFalse(decision["attempts"][1]["retry"])
        self.assertEqual(
            (output / "attempt-1" / "attempt-artifact.txt").read_text(),
            "attempt=1\n",
        )
        self.assertEqual((output / "attempt-artifact.txt").read_text(), "attempt=2\n")
        self.assertIn("GNOME Shell started at", (output / "e2e.log").read_text())
        combined = (output / "combined-e2e.log").read_text()
        self.assertIn("attempt 1/2", combined)
        self.assertIn("attempt 2/2", combined)
        self.assertIn("script=fake.py attempt=2", combined)

    def test_never_retries_after_shell_startup_marker(self) -> None:
        result, output, decision = self.run_modes("startup-timeout,success")
        self.assertEqual(result.returncode, 124)
        self.assertEqual(len(decision["attempts"]), 1)
        self.assertFalse(decision["attempts"][0]["retry"])
        self.assertTrue(decision["attempts"][0]["startup_marker_seen"])
        self.assertFalse((output / "attempt-2").exists())

    def test_never_retries_after_js_start_marker(self) -> None:
        result, output, decision = self.run_modes("js-timeout,success")
        self.assertEqual(result.returncode, 124)
        self.assertEqual(len(decision["attempts"]), 1)
        self.assertTrue(decision["attempts"][0]["js_started"])
        self.assertFalse(decision["attempts"][0]["retry"])
        self.assertFalse((output / "attempt-2").exists())

    def test_never_retries_non_timeout_failure(self) -> None:
        result, output, decision = self.run_modes("failure,success")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(decision["attempts"]), 1)
        self.assertFalse(decision["attempts"][0]["retry"])
        self.assertFalse((output / "attempt-2").exists())

    def test_second_pre_startup_timeout_is_terminal(self) -> None:
        result, output, decision = self.run_modes(
            "pre-startup-timeout,pre-startup-timeout"
        )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(len(decision["attempts"]), 2)
        self.assertTrue(decision["attempts"][0]["retry"])
        self.assertFalse(decision["attempts"][1]["retry"])
        self.assertFalse((output / "attempt-3").exists())
        self.assertEqual((output / "attempt-artifact.txt").read_text(), "attempt=2\n")


if __name__ == "__main__":
    unittest.main()
