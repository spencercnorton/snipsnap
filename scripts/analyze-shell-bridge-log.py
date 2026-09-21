#!/usr/bin/env python3
"""Validate a GNOME Shell bridge acceptance cohort from journal output.

Exit status 0 means the complete cohort and latency gates passed, 1 means the
input could not be parsed, and 2 means a well-formed cohort failed a gate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TextIO


MARKER = "[snipsnap-shell-bridge] "
COMPONENT = "snipsnap-shell-bridge"
SUMMARY_SCHEMA = "snipsnap.shell-bridge-journal-analysis.v1"
DEFAULT_EXPECTED_CAPTURES = 30
DEFAULT_MAX_P50_MS = 100.0
DEFAULT_MAX_P95_MS = 150.0
GLOBAL_EVENTS = frozenset({"enabled", "disabled", "keybinding-unavailable"})
FAILURE_EVENT_FRAGMENTS = ("refused", "failed", "failure", "discarded", "indeterminate")
FAILURE_EVENTS = frozenset({"commit-window-lost"})
EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
STAGE_PATTERN = re.compile(r"^([1-9][0-9]*)[xX]([1-9][0-9]*)$")
BRIDGE_SEVERITY_PATTERN = re.compile(r"\b(?:critical|error)\b", re.IGNORECASE)

MODE_SEQUENCES = {
    "overlay-cancel": (
        "trigger",
        "capture-ready",
        "first-paint",
        "cancelled",
        "overlay-closed",
    ),
    "full-handoff": (
        "trigger",
        "capture-ready",
        "first-paint",
        "selection-started",
        "selection",
        "handoff-started",
        "handoff-encoded",
        "editor-window-observed",
        "commit-accepted",
        "overlay-closed",
        "editor-focus-requested",
    ),
}


class JournalError(ValueError):
    """Raised when a marked journal record is malformed or untrusted."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int_argument(raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _finite_nonnegative_float(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return value


def _stage_argument(raw_value: str) -> tuple[int, int]:
    match = STAGE_PATTERN.fullmatch(raw_value)
    if match is None:
        raise argparse.ArgumentTypeError("must use WIDTHxHEIGHT with positive integers")
    return int(match.group(1)), int(match.group(2))


def _nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        raise JournalError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def read_log(stream: TextIO, source: str = "<stdin>") -> dict:
    """Read marked JSON records while ignoring unrelated journal messages."""

    capture_records: list[tuple[str, dict]] = []
    global_records: list[tuple[str, dict]] = []
    diagnostics: list[str] = []

    for line_number, raw_line in enumerate(stream, start=1):
        location = f"{source}:{line_number}"
        if MARKER not in raw_line:
            lowered = raw_line.lower()
            if COMPONENT in lowered and BRIDGE_SEVERITY_PATTERN.search(raw_line):
                diagnostics.append(f"{location}: bridge critical/error journal line")
            continue

        prefix, payload = raw_line.split(MARKER, 1)
        payload = payload.strip()
        try:
            record = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise JournalError(
                f"{location}: malformed marker JSON: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise JournalError(f"{location}: marker JSON must be an object")
        if record.get("component") != COMPONENT:
            raise JournalError(f"{location}: invalid bridge component")

        event = record.get("event")
        if not isinstance(event, str) or EVENT_PATTERN.fullmatch(event) is None:
            raise JournalError(f"{location}: event must be a canonical non-empty string")
        monotonic_us = record.get("monotonic_us")
        if not _is_int(monotonic_us) or monotonic_us < 1:
            raise JournalError(f"{location}: monotonic_us must be a positive integer")

        if "capture_id" in record:
            capture_id = record["capture_id"]
            if not _is_int(capture_id) or capture_id < 1:
                raise JournalError(f"{location}: capture_id must be a positive integer")
        elif event not in GLOBAL_EVENTS:
            raise JournalError(f"{location}: capture_id must be a positive integer")

        if BRIDGE_SEVERITY_PATTERN.search(prefix):
            diagnostics.append(f"{location}: bridge critical/error journal line")
        if event in GLOBAL_EVENTS:
            global_records.append((location, record))
        else:
            capture_records.append((location, record))

    return {
        "capture_records": capture_records,
        "global_records": global_records,
        "diagnostics": diagnostics,
    }


def _field_positive_int(
    capture_id: int, record: dict, field: str, issues: list[str]
) -> int | None:
    value = record.get(field)
    if not _is_int(value) or value < 1:
        issues.append(
            f"capture {capture_id} {record['event']} {field} must be a positive integer"
        )
        return None
    return value


def _field_topology_id(
    capture_id: int, record: dict, issues: list[str]
) -> str | None:
    value = record.get("topology_id")
    if not isinstance(value, str) or not value.strip():
        issues.append(
            f"capture {capture_id} {record['event']} topology_id must be non-empty"
        )
        return None
    return value


def _event_is_failure(event: str) -> bool:
    return event in FAILURE_EVENTS or any(
        fragment in event for fragment in FAILURE_EVENT_FRAGMENTS
    )


def analyze(
    log: dict,
    expected_stage: tuple[int, int],
    expected_monitors: int,
    mode: str,
) -> dict:
    if mode not in MODE_SEQUENCES:
        raise JournalError(f"unsupported analysis mode: {mode!r}")
    width, height = expected_stage
    if not _is_int(width) or not _is_int(height) or width < 1 or height < 1:
        raise JournalError("expected stage dimensions must be positive integers")
    if not _is_int(expected_monitors) or expected_monitors < 1:
        raise JournalError("expected monitors must be a positive integer")

    by_capture: dict[int, list[tuple[str, dict]]] = defaultdict(list)
    for location, record in log["capture_records"]:
        by_capture[record["capture_id"]].append((location, record))

    issues = list(log["diagnostics"])
    first_paint_elapsed: list[int] = []
    cohort_topologies: set[str] = set()
    required_sequence = MODE_SEQUENCES[mode]
    required_events = frozenset(required_sequence)
    topology_events = {"capture-ready", "first-paint"}
    if mode == "full-handoff":
        topology_events.update(
            {"selection", "handoff-started", "handoff-encoded"}
        )

    for capture_id in sorted(by_capture):
        located_records = by_capture[capture_id]
        records = [record for _location, record in located_records]
        observed_sequence = tuple(record["event"] for record in records)
        if observed_sequence != required_sequence:
            issues.append(
                f"capture {capture_id} journal event order does not match "
                f"the exact {mode!r} lifecycle"
            )
        monotonic_values = [record["monotonic_us"] for record in records]
        if any(
            earlier > later
            for earlier, later in zip(monotonic_values, monotonic_values[1:])
        ):
            issues.append(
                f"capture {capture_id} monotonic_us decreases in journal order"
            )

        for record in records:
            if _event_is_failure(record["event"]):
                issues.append(
                    f"capture {capture_id} contains rejected event {record['event']!r}"
                )
            if record["event"] not in required_events:
                issues.append(
                    f"capture {capture_id} contains unexpected event "
                    f"{record['event']!r} for mode {mode!r}"
                )

        events: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            events[record["event"]].append(record)

        exact_records: dict[str, dict] = {}
        for event in required_sequence:
            count = len(events[event])
            if count != 1:
                issues.append(
                    f"capture {capture_id} requires exactly one {event!r}; found {count}"
                )
            else:
                exact_records[event] = events[event][0]

        if len(exact_records) == len(required_sequence):
            required_times = [
                exact_records[event]["monotonic_us"] for event in required_sequence
            ]
            if any(
                earlier > later
                for earlier, later in zip(required_times, required_times[1:])
            ):
                issues.append(
                    f"capture {capture_id} required lifecycle is not in monotonic order"
                )

        ready = exact_records.get("capture-ready")
        paint = exact_records.get("first-paint")
        ready_topology: str | None = None
        if ready is not None:
            monitors = _field_positive_int(capture_id, ready, "monitors", issues)
            if monitors is not None and monitors != expected_monitors:
                issues.append(
                    f"capture {capture_id} reports {monitors} monitors; expected "
                    f"{expected_monitors}"
                )
            ready_width = _field_positive_int(capture_id, ready, "stage_width", issues)
            ready_height = _field_positive_int(capture_id, ready, "stage_height", issues)
            if (ready_width, ready_height) != (width, height):
                if ready_width is not None and ready_height is not None:
                    issues.append(
                        f"capture {capture_id} capture-ready stage "
                        f"{ready_width}x{ready_height} does not match expected "
                        f"{width}x{height}"
                    )
            ready_topology = _field_topology_id(capture_id, ready, issues)
            if ready_topology is not None:
                cohort_topologies.add(ready_topology)

        if paint is not None:
            paint_width = _field_positive_int(capture_id, paint, "stage_width", issues)
            paint_height = _field_positive_int(capture_id, paint, "stage_height", issues)
            if (paint_width, paint_height) != (width, height):
                if paint_width is not None and paint_height is not None:
                    issues.append(
                        f"capture {capture_id} first-paint stage "
                        f"{paint_width}x{paint_height} does not match expected "
                        f"{width}x{height}"
                    )
            elapsed_us = paint.get("elapsed_us")
            if not _is_int(elapsed_us) or elapsed_us < 0:
                issues.append(
                    f"capture {capture_id} first-paint elapsed_us must be a "
                    "non-negative integer"
                )
            else:
                first_paint_elapsed.append(elapsed_us)

        for event in sorted(topology_events):
            record = exact_records.get(event)
            if record is None or record is ready:
                continue
            topology_id = _field_topology_id(capture_id, record, issues)
            if (
                ready_topology is not None
                and topology_id is not None
                and topology_id != ready_topology
            ):
                issues.append(
                    f"capture {capture_id} topology mismatch: "
                    f"{event} reports {topology_id!r}, expected "
                    f"{ready_topology!r}"
                )

        overlay_closed = exact_records.get("overlay-closed")
        if mode == "overlay-cancel":
            cancelled = exact_records.get("cancelled")
            if cancelled is not None and cancelled.get("reason") != "escape":
                issues.append(
                    f"capture {capture_id} cancelled reason must equal 'escape'"
                )
            if overlay_closed is not None and overlay_closed.get("reason") != "escape":
                issues.append(
                    f"capture {capture_id} overlay-closed reason must equal 'escape'"
                )
        elif (
            overlay_closed is not None
            and overlay_closed.get("reason") != "commit-accepted"
        ):
            issues.append(
                f"capture {capture_id} overlay-closed reason must equal "
                "'commit-accepted'"
            )

    if len(cohort_topologies) > 1:
        issues.append(
            "cohort topology mismatch: " + ", ".join(sorted(cohort_topologies))
        )

    for _location, record in log["global_records"]:
        issues.append(f"cohort contains rejected global event {record['event']!r}")

    latency = None
    if first_paint_elapsed:
        latency = {
            "samples": len(first_paint_elapsed),
            "p50_ms": _nearest_rank(first_paint_elapsed, 0.50) / 1000,
            "p95_ms": _nearest_rank(first_paint_elapsed, 0.95) / 1000,
            "max_ms": max(first_paint_elapsed) / 1000,
        }

    return {
        "schema": SUMMARY_SCHEMA,
        "mode": mode,
        "expected_monitors": expected_monitors,
        "expected_stage": {"width": width, "height": height},
        "captures": len(by_capture),
        "capture_ids": sorted(by_capture),
        "topology_id": next(iter(cohort_topologies))
        if len(cohort_topologies) == 1
        else None,
        "first_paint": latency,
        "issues": issues,
    }


def evaluate_gate(
    summary: dict,
    expected_captures: int,
    maximum_p50_ms: float,
    maximum_p95_ms: float,
) -> list[str]:
    if expected_captures < 1:
        raise JournalError("expected captures must be at least 1")
    for name, value in (
        ("maximum p50", maximum_p50_ms),
        ("maximum p95", maximum_p95_ms),
    ):
        if not math.isfinite(value) or value < 0:
            raise JournalError(f"{name} must be finite and non-negative")

    failures = list(summary["issues"])
    if summary["captures"] != expected_captures:
        failures.append(
            f"requires exactly {expected_captures} captures; found "
            f"{summary['captures']}"
        )

    latency = summary["first_paint"]
    if latency is None:
        failures.append("first-paint has no valid elapsed_us samples")
    else:
        if latency["samples"] != summary["captures"]:
            failures.append(
                f"first-paint samples {latency['samples']} do not match "
                f"captures {summary['captures']}"
            )
        if latency["p50_ms"] > maximum_p50_ms:
            failures.append(
                f"first-paint p50 {latency['p50_ms']:.3f}ms exceeds "
                f"{maximum_p50_ms:.3f}ms"
            )
        if latency["p95_ms"] > maximum_p95_ms:
            failures.append(
                f"first-paint p95 {latency['p95_ms']:.3f}ms exceeds "
                f"{maximum_p95_ms:.3f}ms"
            )
    return failures


def _human_summary(summary: dict) -> str:
    stage = summary["expected_stage"]
    lines = [
        f"mode: {summary['mode']}",
        f"expected monitors: {summary['expected_monitors']}",
        f"expected stage: {stage['width']}x{stage['height']}",
        f"captures: {summary['captures']}",
        f"topology: {summary['topology_id'] or 'indeterminate'}",
    ]
    latency = summary["first_paint"]
    if latency is None:
        lines.append("first-paint: no valid samples")
    else:
        lines.append(
            f"first-paint: n={latency['samples']} "
            f"p50={latency['p50_ms']:.3f}ms "
            f"p95={latency['p95_ms']:.3f}ms "
            f"max={latency['max_ms']:.3f}ms"
        )
    gate = summary["gate"]
    lines.append(f"gate: {'pass' if gate['passed'] else 'fail'}")
    lines.extend(f"  - {failure}" for failure in gate["failures"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", default="-", metavar="LOG")
    parser.add_argument(
        "--expected-captures",
        type=_positive_int_argument,
        default=DEFAULT_EXPECTED_CAPTURES,
        help=f"exact capture count required (default: {DEFAULT_EXPECTED_CAPTURES})",
    )
    parser.add_argument("--mode", choices=sorted(MODE_SEQUENCES), required=True)
    parser.add_argument(
        "--expected-monitors",
        type=_positive_int_argument,
        required=True,
        help="exact monitor count required in every capture-ready record",
    )
    parser.add_argument(
        "--max-p50-ms",
        type=_finite_nonnegative_float,
        default=DEFAULT_MAX_P50_MS,
        help=f"maximum first-paint p50 (default: {DEFAULT_MAX_P50_MS:g}ms)",
    )
    parser.add_argument(
        "--max-p95-ms",
        type=_finite_nonnegative_float,
        default=DEFAULT_MAX_P95_MS,
        help=f"maximum first-paint p95 (default: {DEFAULT_MAX_P95_MS:g}ms)",
    )
    parser.add_argument(
        "--expected-stage",
        type=_stage_argument,
        required=True,
        metavar="WIDTHxHEIGHT",
    )
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)

    stream: TextIO
    opened: TextIO | None = None
    source = "<stdin>"
    try:
        if args.log == "-":
            stream = sys.stdin
        else:
            path = Path(args.log)
            opened = path.open("r", encoding="utf-8")
            stream = opened
            source = str(path)
        log = read_log(stream, source)
        summary = analyze(
            log,
            args.expected_stage,
            args.expected_monitors,
            args.mode,
        )
        failures = evaluate_gate(
            summary,
            args.expected_captures,
            args.max_p50_ms,
            args.max_p95_ms,
        )
    except (OSError, JournalError) as exc:
        print(f"shell bridge log error: {exc}", file=sys.stderr)
        return 1
    finally:
        if opened is not None:
            opened.close()

    summary["gate"] = {
        "passed": not failures,
        "expected_captures": args.expected_captures,
        "maximum_p50_ms": args.max_p50_ms,
        "maximum_p95_ms": args.max_p95_ms,
        "failures": failures,
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        print(_human_summary(summary))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
