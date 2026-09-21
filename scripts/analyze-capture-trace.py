#!/usr/bin/env python3
"""Summarize opt-in SnipSnap capture trace JSONL.

The trace contains monotonic elapsed durations only.  It deliberately excludes
wall-clock timestamps, image data, filenames, window titles, and portal URIs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, TextIO


TRACE_SCHEMA = "snipsnap.capture-trace.v1"
STDERR_PREFIX = "SNIPSNAP_CAPTURE_TRACE "
DEFAULT_TARGET_EVENT = "capture_widget_paint_completed"
DEFAULT_MIN_CAPTURES = 30
LEGACY_RUN_ID = "__legacy__"


class TraceError(ValueError):
    """Raised when a trace record does not follow the public trace schema."""


def _nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        raise TraceError("cannot calculate a percentile without samples")
    if percentile <= 0 or percentile > 1:
        raise TraceError("percentile must be in the interval (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _finite_nonnegative_float(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return value


def _positive_int(raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _record_run_id(record: dict) -> str:
    return record.get("run_id", LEGACY_RUN_ID)


def _select_run(
    records: list[dict], requested_run_id: str | None
) -> tuple[list[dict], str | None, list[str]]:
    available = sorted(
        {_record_run_id(record) for record in records},
        key=lambda value: (value != LEGACY_RUN_ID, value),
    )

    if requested_run_id is not None:
        selected = [
            record for record in records if _record_run_id(record) == requested_run_id
        ]
        if not selected:
            available_text = ", ".join(available) if available else "none"
            raise TraceError(
                f"run_id {requested_run_id!r} was not found; available run IDs: "
                f"{available_text}"
            )
        return selected, requested_run_id, available

    if len(available) > 1:
        raise TraceError(
            "multiple trace run IDs found "
            f"({', '.join(available)}); pass --run-id to select exactly one"
        )

    selected_run_id = available[0] if available else None
    return records, selected_run_id, available


def read_records(streams: Iterable[tuple[str, TextIO]]) -> list[dict]:
    records: list[dict] = []

    for source, stream in streams:
        for line_number, raw_line in enumerate(stream, start=1):
            payload = raw_line.strip()
            if not payload:
                continue
            if payload.startswith(STDERR_PREFIX):
                payload = payload[len(STDERR_PREFIX) :]
            elif not payload.startswith("{"):
                # Stderr mode intentionally shares the stream with ordinary
                # SnipSnap diagnostics. Only explicitly prefixed trace lines
                # and raw JSONL file records are part of this format.
                continue
            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise TraceError(
                    f"{source}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc

            if not isinstance(record, dict):
                raise TraceError(f"{source}:{line_number}: record must be an object")
            if record.get("schema") != TRACE_SCHEMA:
                raise TraceError(f"{source}:{line_number}: unsupported trace schema")

            capture_id = record.get("capture_id")
            event = record.get("event")
            elapsed_us = record.get("elapsed_us")
            if not isinstance(capture_id, int) or isinstance(capture_id, bool):
                raise TraceError(
                    f"{source}:{line_number}: capture_id must be an integer"
                )
            if not isinstance(event, str) or not event:
                raise TraceError(
                    f"{source}:{line_number}: event must be a non-empty string"
                )
            if (
                not isinstance(elapsed_us, int)
                or isinstance(elapsed_us, bool)
                or elapsed_us < 0
            ):
                raise TraceError(
                    f"{source}:{line_number}: elapsed_us must be a non-negative integer"
                )

            if "run_id" in record:
                run_id = record["run_id"]
                if not isinstance(run_id, str) or not run_id.strip():
                    raise TraceError(
                        f"{source}:{line_number}: run_id must be a non-empty string"
                    )
                if run_id == LEGACY_RUN_ID:
                    raise TraceError(
                        f"{source}:{line_number}: run_id {LEGACY_RUN_ID!r} is reserved"
                    )

            fields = record.get("fields", {})
            if not isinstance(fields, dict):
                raise TraceError(f"{source}:{line_number}: fields must be an object")

            # A backend may legitimately report the same stage more than once
            # (for example, a duplicate portal response that the request state
            # machine subsequently ignores). Keep those samples visible rather
            # than rejecting the complete benchmark file. Terminal success is
            # still counted once per capture below.
            records.append(record)

    return records


def summarize(
    records: list[dict], target_event: str, run_id: str | None = None
) -> dict:
    records, selected_run_id, available_run_ids = _select_run(records, run_id)
    by_capture: dict[int, list[dict]] = defaultdict(list)

    for record in records:
        by_capture[record["capture_id"]].append(record)

    # Percentiles describe captures, not raw records. For a repeated backend
    # event, use the first monotonic occurrence for that capture and report the
    # additional records separately instead of allowing one capture to dominate
    # the distribution.
    by_event: dict[str, list[int]] = defaultdict(list)
    raw_samples_by_event: dict[str, int] = defaultdict(int)
    duplicate_captures_by_event: dict[str, int] = defaultdict(int)
    for capture_records in by_capture.values():
        capture_events: dict[str, list[int]] = defaultdict(list)
        for record in capture_records:
            capture_events[record["event"]].append(record["elapsed_us"])
        for event, values in capture_events.items():
            by_event[event].append(min(values))
            raw_samples_by_event[event] += len(values)
            if len(values) > 1:
                duplicate_captures_by_event[event] += 1

    event_summaries = {}
    for event, values in sorted(by_event.items()):
        raw_samples = raw_samples_by_event[event]
        event_summaries[event] = {
            "samples": len(values),
            "raw_samples": raw_samples,
            "duplicate_samples": raw_samples - len(values),
            "duplicate_captures": duplicate_captures_by_event[event],
            "p50_ms": _nearest_rank(values, 0.50) / 1000,
            "p95_ms": _nearest_rank(values, 0.95) / 1000,
            "max_ms": max(values) / 1000,
        }

    failed_captures = set()
    successful_captures = set()
    incomplete_captures = set()
    inconsistent_captures = set()
    sources: dict[str, int] = defaultdict(int)
    origins: dict[str, int] = defaultdict(int)
    for capture_id, capture_records in by_capture.items():
        events = {record["event"] for record in capture_records}
        start_records = [
            record
            for record in capture_records
            if record["event"] == "capture_requested"
        ]
        target_records = [
            record for record in capture_records if record["event"] == target_event
        ]
        has_target = bool(target_records)
        has_failure = "capture_failed" in events or "capture_rejected" in events
        terminal_records = [
            record
            for record in capture_records
            if record["event"]
            in {
                "capture_completed",
                "capture_cancelled",
                "capture_failed",
                "capture_rejected",
            }
        ]

        if start_records:
            start_fields = start_records[0].get("fields", {})
            source = start_fields.get("source", "unknown")
            origin = start_fields.get("origin")
            if not isinstance(source, str) or not source:
                source = "unknown"
            if not isinstance(origin, str) or not origin:
                origin = "process_start" if "process_started" in events else "unknown"
        else:
            source = "missing"
            origin = "missing"
        sources[source] += 1
        origins[origin] += 1

        elapsed_values = [record["elapsed_us"] for record in capture_records]
        ordered = all(
            earlier <= later
            for earlier, later in zip(elapsed_values, elapsed_values[1:])
        )
        terminal_is_last = (
            len(terminal_records) == 1 and capture_records[-1] is terminal_records[0]
        )
        lifecycle_ordered = False
        if len(start_records) == 1 and len(target_records) == 1 and terminal_is_last:
            start_us = start_records[0]["elapsed_us"]
            target_us = target_records[0]["elapsed_us"]
            terminal_us = terminal_records[0]["elapsed_us"]
            lifecycle_ordered = start_us <= target_us <= terminal_us

        if (
            len(start_records) != 1
            or len(target_records) > 1
            or len(terminal_records) > 1
            or (has_target and has_failure)
            or not ordered
            or (len(terminal_records) == 1 and not terminal_is_last)
            or (has_target and len(terminal_records) == 1 and not lifecycle_ordered)
        ):
            inconsistent_captures.add(capture_id)
        elif has_failure:
            failed_captures.add(capture_id)
        elif has_target and len(terminal_records) == 1:
            successful_captures.add(capture_id)
        else:
            incomplete_captures.add(capture_id)

    duplicate_event_records = sum(
        event["duplicate_samples"] for event in event_summaries.values()
    )

    return {
        "schema": TRACE_SCHEMA,
        "run_id": selected_run_id,
        "available_run_ids": available_run_ids,
        "captures": len(by_capture),
        "successful_captures": len(successful_captures),
        "failed_or_rejected_captures": len(failed_captures),
        "incomplete_captures": len(incomplete_captures),
        "inconsistent_captures": len(inconsistent_captures),
        "sources": dict(sorted(sources.items())),
        "origins": dict(sorted(origins.items())),
        "duplicate_event_records": duplicate_event_records,
        "target_event": target_event,
        "target": event_summaries.get(target_event),
        "events": event_summaries,
    }


def evaluate_gate(
    summary: dict, maximum_p95_ms: float, minimum_captures: int
) -> list[str]:
    if not math.isfinite(maximum_p95_ms) or maximum_p95_ms < 0:
        raise TraceError("maximum p95 must be finite and non-negative")
    if minimum_captures < 1:
        raise TraceError("minimum captures must be at least 1")

    failures = []
    captures = summary["captures"]
    if captures < minimum_captures:
        failures.append(
            f"requires at least {minimum_captures} captures; found {captures}"
        )
    if summary["failed_or_rejected_captures"]:
        failures.append(
            f"{summary['failed_or_rejected_captures']} captures failed or were rejected"
        )
    if summary["incomplete_captures"]:
        failures.append(f"{summary['incomplete_captures']} captures are incomplete")
    if summary["inconsistent_captures"]:
        failures.append(
            f"{summary['inconsistent_captures']} captures have inconsistent lifecycles"
        )
    if len(summary["sources"]) > 1:
        failures.append(
            "capture cohort mixes sources: " + ", ".join(summary["sources"])
        )
    if len(summary["origins"]) > 1:
        failures.append(
            "capture cohort mixes timeline origins: " + ", ".join(summary["origins"])
        )

    target = summary["target"]
    if target is None:
        failures.append(f"target event {summary['target_event']!r} has no samples")
    elif target["p95_ms"] > maximum_p95_ms:
        failures.append(
            f"target p95 {target['p95_ms']:.3f}ms exceeds " f"{maximum_p95_ms:.3f}ms"
        )

    return failures


def _open_streams(paths: list[str]) -> tuple[list[tuple[str, TextIO]], list[TextIO]]:
    if not paths:
        return [("<stdin>", sys.stdin)], []

    streams: list[tuple[str, TextIO]] = []
    opened: list[TextIO] = []
    for path_string in paths:
        if path_string == "-":
            streams.append(("<stdin>", sys.stdin))
            continue
        path = Path(path_string)
        stream = path.open("r", encoding="utf-8")
        opened.append(stream)
        streams.append((str(path), stream))
    return streams, opened


def _human_summary(summary: dict) -> str:
    lines = [
        f"run_id: {summary['run_id'] or 'none'}",
        f"captures: {summary['captures']}",
        f"successful: {summary['successful_captures']}",
        f"failed/rejected: {summary['failed_or_rejected_captures']}",
        f"incomplete: {summary['incomplete_captures']}",
        f"inconsistent: {summary['inconsistent_captures']}",
        f"sources: {json.dumps(summary['sources'], sort_keys=True)}",
        f"origins: {json.dumps(summary['origins'], sort_keys=True)}",
        f"duplicate event records: {summary['duplicate_event_records']}",
    ]
    target = summary["target"]
    if target is None:
        lines.append(f"{summary['target_event']}: no samples")
    else:
        lines.append(
            f"{summary['target_event']}: n={target['samples']} "
            f"p50={target['p50_ms']:.3f}ms "
            f"p95={target['p95_ms']:.3f}ms max={target['max_ms']:.3f}ms"
        )
    gate = summary.get("gate")
    if gate is not None:
        lines.append(f"gate: {'pass' if gate['passed'] else 'fail'}")
        lines.extend(f"  - {failure}" for failure in gate["failures"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="trace JSONL paths; default stdin")
    parser.add_argument(
        "--target-event", default=DEFAULT_TARGET_EVENT, help="event used for success"
    )
    parser.add_argument(
        "--run-id",
        help=(
            "analyze exactly one run ID; use "
            f"{LEGACY_RUN_ID!r} for records without run_id"
        ),
    )
    parser.add_argument(
        "--max-p95-ms",
        type=_finite_nonnegative_float,
        help="exit 2 unless the target event has samples and p95 is at most this value",
    )
    parser.add_argument(
        "--min-captures",
        type=_positive_int,
        default=DEFAULT_MIN_CAPTURES,
        help=(
            "minimum complete capture cohort required by --max-p95-ms "
            f"(default: {DEFAULT_MIN_CAPTURES})"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args(argv)

    opened: list[TextIO] = []
    try:
        streams, opened = _open_streams(args.paths)
        summary = summarize(read_records(streams), args.target_event, args.run_id)
    except (OSError, TraceError) as exc:
        print(f"capture trace error: {exc}", file=sys.stderr)
        return 1
    finally:
        for stream in opened:
            stream.close()

    gate_failures = []
    if args.max_p95_ms is not None:
        gate_failures = evaluate_gate(summary, args.max_p95_ms, args.min_captures)
        summary["gate"] = {
            "passed": not gate_failures,
            "minimum_captures": args.min_captures,
            "maximum_p95_ms": args.max_p95_ms,
            "failures": gate_failures,
        }

    if args.json:
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        print(_human_summary(summary))

    if gate_failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
