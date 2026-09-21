#!/usr/bin/env python3
"""Focused tests for the GNOME Shell perf-helper startup supervisor."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


SUPERVISOR = (
    Path(__file__).parent
    / "gnome-shell-perf-helper"
    / "gnome-shell-perf-helper"
)


class PerfHelperSupervisorTest(unittest.TestCase):
    def _run(self, helper_body: str, *arguments: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        attempts = root / "attempts"
        ready = root / "ready"
        arguments_seen = root / "arguments"
        helper = root / "helper"
        helper.write_text("#!/bin/sh\n" + textwrap.dedent(helper_body))
        helper.chmod(0o755)
        probe = root / "gdbus"
        probe.write_text(
            "#!/bin/sh\n"
            "test -f \"$TEST_READY\" && "
            "printf '(true,)\\n' || printf '(false,)\\n'\n"
        )
        probe.chmod(0o755)
        environment = {
            **os.environ,
            "SNIPSNAP_REAL_PERF_HELPER": str(helper),
            "SNIPSNAP_PERF_HELPER_PROBE": str(probe),
            "SNIPSNAP_PERF_HELPER_ATTEMPTS": "3",
            "SNIPSNAP_PERF_HELPER_PROBES": "20",
            "SNIPSNAP_PERF_HELPER_PROBE_INTERVAL": "0.02",
            "SNIPSNAP_PERF_HELPER_STOP_PROBES": "3",
            "TEST_ATTEMPTS": str(attempts),
            "TEST_READY": str(ready),
            "TEST_ARGUMENTS": str(arguments_seen),
        }
        result = subprocess.run(
            [str(SUPERVISOR), *arguments],
            env=environment,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result, root

    def test_passes_arguments_and_does_not_retry_ready_helper(self) -> None:
        result, root = self._run(
            """
            printf '1\\n' >> "$TEST_ATTEMPTS"
            printf '%s\\n' "$*" > "$TEST_ARGUMENTS"
            : > "$TEST_READY"
            sleep 0.1
            """,
            "--idle-timeout",
            "7",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "attempts").read_text().splitlines(), ["1"])
        self.assertEqual((root / "arguments").read_text(), "--idle-timeout 7\n")
        self.assertNotIn("retrying", result.stderr)

    def test_retries_a_live_helper_that_never_owns_the_name(self) -> None:
        result, root = self._run(
            """
            count=0
            test -f "$TEST_ATTEMPTS" && count=$(wc -l < "$TEST_ATTEMPTS")
            count=$((count + 1))
            printf '%s\\n' "$count" >> "$TEST_ATTEMPTS"
            if [ "$count" -eq 2 ]; then
                : > "$TEST_READY"
                sleep 0.1
                exit 0
            fi
            sleep 5 &
            sleeper=$!
            trap 'kill "$sleeper" 2>/dev/null || true; exit 0' TERM
            wait "$sleeper"
            """
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "attempts").read_text().splitlines(), ["1", "2"])
        self.assertIn("did not own its D-Bus name; retrying (1/3)", result.stderr)

    def test_fails_after_bounded_early_exits(self) -> None:
        result, root = self._run(
            """
            printf '1\\n' >> "$TEST_ATTEMPTS"
            sleep 0.01
            exit 23
            """
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(len((root / "attempts").read_text().splitlines()), 3)
        self.assertIn("exited before owning its D-Bus name (status 23)", result.stderr)
        self.assertIn("failed to own its D-Bus name after 3 attempts", result.stderr)


if __name__ == "__main__":
    unittest.main()
