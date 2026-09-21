#!/usr/bin/env python3
"""Run a GNOME E2E command with one pre-startup-stall-only retry."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


STARTUP_MARKER = "GNOME Shell started at"
JS_START_MARKER = "SNIPSNAP-E2E-JS-START"
TIMEOUT_EXIT = 124
MAX_ATTEMPTS = 2


def stream_attempt(
    command: Sequence[str], output: Path, environ: dict[str, str]
) -> tuple[int, bool, bool]:
    """Stream one attempt while retaining the exact combined output."""

    startup_seen = False
    js_started = False
    with output.open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                command,
                env=environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as error:
            line = f"cannot start GNOME E2E command: {error}\n"
            log.write(line)
            sys.stdout.write(line)
            return 127, False, False

        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
            startup_seen = startup_seen or STARTUP_MARKER in line
            js_started = js_started or JS_START_MARKER in line
        return process.wait(), startup_seen, js_started


def promote_attempt(attempt_dir: Path, output_dir: Path) -> None:
    """Expose the terminal attempt at the paths consumed by CI assertions."""

    for source in attempt_dir.iterdir():
        destination = output_dir / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


def safe_attempt_directory(output_dir: Path, attempt_number: int) -> Path:
    """Create a fresh non-symlink attempt directory."""

    attempt_dir = output_dir / f"attempt-{attempt_number}"
    if attempt_dir.is_symlink():
        raise SystemExit(f"unsafe E2E attempt directory: {attempt_dir}")
    try:
        attempt_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise SystemExit(f"E2E attempt directory already exists: {attempt_dir}") from error
    return attempt_dir


def write_decision(output_dir: Path, attempts: list[dict[str, object]]) -> None:
    value = {
        "schema": "snipsnap.gnome-e2e-startup-retry.v1",
        "startup_marker": STARTUP_MARKER,
        "js_start_marker": JS_START_MARKER,
        "maximum_attempts": MAX_ATTEMPTS,
        "attempts": attempts,
    }
    (output_dir / "startup-retry.json").write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    if arguments.command[:1] == ["--"]:
        arguments.command = arguments.command[1:]
    if not arguments.command:
        parser.error("a command is required after --")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.output_dir.is_symlink():
        raise SystemExit(f"unsafe E2E output directory: {arguments.output_dir}")
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not output_dir.is_dir():
        raise SystemExit(f"unsafe E2E output directory: {output_dir}")

    attempts: list[dict[str, object]] = []
    terminal_exit = 127
    script_candidates = [
        Path(argument).name
        for argument in arguments.command
        if Path(argument).suffix in {".js", ".py"}
    ]
    script_name = (
        script_candidates[-1]
        if script_candidates
        else Path(arguments.command[0]).name
    )
    combined_log = output_dir / "combined-e2e.log"
    decision_log = output_dir / "startup-retry.json"
    for reserved in (combined_log, decision_log):
        if reserved.exists() or reserved.is_symlink():
            raise SystemExit(f"E2E evidence path already exists: {reserved}")
    combined_log.write_text("", encoding="utf-8")
    for attempt_number in range(1, MAX_ATTEMPTS + 1):
        attempt_dir = safe_attempt_directory(output_dir, attempt_number)
        attempt_log = attempt_dir / "e2e.log"
        environ = dict(os.environ)
        environ["GNOME_SHELL_E2E_OUTPUT_DIR"] = str(attempt_dir)
        environ["SNIPSNAP_E2E_ATTEMPT"] = str(attempt_number)
        environ["SNIPSNAP_E2E_SCRIPT"] = script_name
        heading = (
            f"=== GNOME E2E {script_name} attempt "
            f"{attempt_number}/{MAX_ATTEMPTS} ==="
        )
        print(heading, flush=True)
        terminal_exit, startup_seen, js_started = stream_attempt(
            arguments.command, attempt_log, environ
        )
        with combined_log.open("a", encoding="utf-8") as combined:
            combined.write(f"{heading}\n")
            combined.write(attempt_log.read_text(encoding="utf-8"))
            combined.write(f"=== attempt exit {terminal_exit} ===\n")
        retry = (
            attempt_number == 1
            and terminal_exit == TIMEOUT_EXIT
            and not startup_seen
            and not js_started
        )
        attempts.append(
            {
                "attempt": attempt_number,
                "directory": attempt_dir.name,
                "exit_code": terminal_exit,
                "script": script_name,
                "startup_marker_seen": startup_seen,
                "js_started": js_started,
                "retry": retry,
            }
        )
        write_decision(output_dir, attempts)
        if retry:
            print(
                "GNOME E2E timed out before Shell startup and before JS; "
                "retrying the complete bounded harness once",
                flush=True,
            )
            continue

        promote_attempt(attempt_dir, output_dir)
        return terminal_exit

    # The second attempt is terminal even when it has the same failure shape.
    promote_attempt(output_dir / f"attempt-{MAX_ATTEMPTS}", output_dir)
    return terminal_exit


if __name__ == "__main__":
    raise SystemExit(main())
