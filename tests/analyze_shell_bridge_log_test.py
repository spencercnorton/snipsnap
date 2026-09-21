#!/usr/bin/env python3

import argparse
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "analyze-shell-bridge-log.py"
if not SCRIPT.is_file():
    raise unittest.SkipTest("scripts/ is not part of this tree")
SPEC = importlib.util.spec_from_file_location("analyze_shell_bridge_log", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

STAGE = (11_520, 2_160)


def bridge_event(event, capture_id=None, monotonic_us=1, **fields):
    record = {
        "component": MODULE.COMPONENT,
        "event": event,
        "monotonic_us": monotonic_us,
        **fields,
    }
    if capture_id is not None:
        record["capture_id"] = capture_id
    return record


def valid_capture(capture_id, mode="overlay-cancel", elapsed_us=80_000):
    base = capture_id * 1_000_000
    topology_id = "v1-reference"
    records = [
        bridge_event("trigger", capture_id, base + 10),
        bridge_event(
            "capture-ready",
            capture_id,
            base + 20,
            stage_width=STAGE[0],
            stage_height=STAGE[1],
            monitors=3,
            topology_id=topology_id,
            elapsed_us=max(0, elapsed_us - 10_000),
        ),
        bridge_event(
            "first-paint",
            capture_id,
            base + 30,
            stage_width=STAGE[0],
            stage_height=STAGE[1],
            topology_id=topology_id,
            elapsed_us=elapsed_us,
        ),
    ]
    if mode == "overlay-cancel":
        records.extend(
            [
                bridge_event(
                    "cancelled", capture_id, base + 40, reason="escape"
                ),
                bridge_event(
                    "overlay-closed", capture_id, base + 50, reason="escape"
                ),
            ]
        )
    elif mode == "full-handoff":
        events = (
            ("selection-started", {}),
            ("selection", {"topology_id": topology_id}),
            ("handoff-started", {"topology_id": topology_id}),
            ("handoff-encoded", {"topology_id": topology_id}),
            ("editor-window-observed", {}),
            ("commit-accepted", {}),
            ("overlay-closed", {"reason": "commit-accepted"}),
            ("editor-focus-requested", {}),
        )
        for offset, (event, fields) in enumerate(events, start=4):
            records.append(
                bridge_event(event, capture_id, base + offset * 10, **fields)
            )
    else:
        raise AssertionError(f"unknown fixture mode: {mode}")
    return records


def marked_lines(records, unrelated=()):
    lines = list(unrelated)
    lines.extend(
        f"GNOME Shell-Message: {MODULE.MARKER}{json.dumps(record)}"
        for record in records
    )
    return "\n".join(lines) + "\n"


def parse(records, unrelated=()):
    return MODULE.read_log(io.StringIO(marked_lines(records, unrelated)), "test.log")


def summarize(records, mode="overlay-cancel"):
    return MODULE.analyze(parse(records), STAGE, 3, mode)


def event_named(records, name):
    return next(record for record in records if record["event"] == name)


class AnalyzeShellBridgeLogTest(unittest.TestCase):
    def assertGatePasses(self, summary, expected):
        self.assertEqual(MODULE.evaluate_gate(summary, expected, 100, 150), [])

    def assertGateFailsWith(self, summary, expected, pattern):
        failures = MODULE.evaluate_gate(summary, expected, 100, 150)
        self.assertTrue(
            any(pattern in failure for failure in failures),
            f"{pattern!r} not found in {failures!r}",
        )

    def test_defaults_define_exact_thirty_capture_latency_gate(self):
        self.assertEqual(MODULE.DEFAULT_EXPECTED_CAPTURES, 30)
        self.assertEqual(MODULE.DEFAULT_MAX_P50_MS, 100)
        self.assertEqual(MODULE.DEFAULT_MAX_P95_MS, 150)

    def test_overlay_cancel_cohort_passes_and_ignores_unrelated_lines(self):
        records = valid_capture(1) + valid_capture(2)
        log = parse(
            records,
            unrelated=(
                "ordinary gnome-shell diagnostic",
                "unrelated application CRITICAL error",
            ),
        )
        summary = MODULE.analyze(log, STAGE, 3, "overlay-cancel")

        self.assertEqual(summary["captures"], 2)
        self.assertEqual(summary["topology_id"], "v1-reference")
        self.assertEqual(summary["first_paint"]["samples"], 2)
        self.assertGatePasses(summary, 2)

    def test_full_handoff_cohort_requires_complete_ordered_contract(self):
        summary = summarize(valid_capture(1, "full-handoff"), "full-handoff")

        self.assertGatePasses(summary, 1)

    def test_percentiles_use_nearest_rank(self):
        records = []
        for capture_id in range(1, 21):
            records.extend(valid_capture(capture_id, elapsed_us=capture_id * 1_000))
        summary = summarize(records)

        self.assertEqual(summary["first_paint"]["p50_ms"], 10)
        self.assertEqual(summary["first_paint"]["p95_ms"], 19)
        self.assertEqual(summary["first_paint"]["max_ms"], 20)
        self.assertGatePasses(summary, 20)

    def test_gate_requires_exact_capture_count(self):
        one = summarize(valid_capture(1))
        three = summarize(
            valid_capture(1) + valid_capture(2) + valid_capture(3)
        )

        self.assertGateFailsWith(one, 2, "requires exactly 2 captures; found 1")
        self.assertGateFailsWith(three, 2, "requires exactly 2 captures; found 3")

    def test_gate_rejects_p50_and_p95_over_budget(self):
        p50_summary = summarize(
            valid_capture(1, elapsed_us=100_001)
            + valid_capture(2, elapsed_us=100_001)
        )
        p95_summary = summarize(
            valid_capture(1, elapsed_us=100_000)
            + valid_capture(2, elapsed_us=150_001)
        )

        self.assertGateFailsWith(p50_summary, 2, "first-paint p50")
        self.assertGateFailsWith(p95_summary, 2, "first-paint p95")

    def test_latency_thresholds_are_inclusive(self):
        summary = summarize(
            valid_capture(1, elapsed_us=100_000)
            + valid_capture(2, elapsed_us=150_000)
        )

        failures = MODULE.evaluate_gate(summary, 2, 150, 150)
        self.assertEqual(failures, [])

    def test_rejects_malformed_marker_json_and_non_object(self):
        for payload, pattern in (
            ("{broken", "malformed marker JSON"),
            ("[]", "must be an object"),
        ):
            with self.subTest(payload=payload):
                stream = io.StringIO(f"prefix {MODULE.MARKER}{payload}\n")
                with self.assertRaisesRegex(MODULE.JournalError, pattern):
                    MODULE.read_log(stream, "mutant")

    def test_rejects_component_and_event_mutations(self):
        base = bridge_event("trigger", 1, 10)
        mutations = (
            ("component", "other", "component"),
            ("event", "", "event"),
            ("event", "First Paint", "event"),
            ("event", 7, "event"),
        )
        for field, value, pattern in mutations:
            with self.subTest(field=field, value=value):
                record = copy.deepcopy(base)
                record[field] = value
                with self.assertRaisesRegex(MODULE.JournalError, pattern):
                    parse([record])

    def test_rejects_nonpositive_or_boolean_capture_ids(self):
        for value in (0, -1, False, "1", None):
            with self.subTest(value=value):
                record = bridge_event("trigger", 1, 10)
                record["capture_id"] = value
                with self.assertRaisesRegex(MODULE.JournalError, "capture_id"):
                    parse([record])

    def test_rejects_nonpositive_or_boolean_monotonic_values(self):
        for value in (0, -1, False, "1", None):
            with self.subTest(value=value):
                record = bridge_event("trigger", 1, 10)
                record["monotonic_us"] = value
                with self.assertRaisesRegex(MODULE.JournalError, "monotonic_us"):
                    parse([record])

    def test_globals_may_omit_capture_id_but_capture_events_may_not(self):
        MODULE.read_log(
            io.StringIO(
                marked_lines(
                    [
                        bridge_event("enabled", monotonic_us=1),
                        bridge_event("disabled", monotonic_us=2),
                        bridge_event("keybinding-unavailable", monotonic_us=3),
                    ]
                )
            )
        )
        missing = bridge_event("trigger", monotonic_us=4)

        with self.assertRaisesRegex(MODULE.JournalError, "capture_id"):
            parse([missing])

    def test_common_lifecycle_events_are_required_exactly_once(self):
        for event in ("trigger", "capture-ready", "first-paint", "overlay-closed"):
            with self.subTest(event=event, mutation="missing"):
                records = [
                    record for record in valid_capture(1) if record["event"] != event
                ]
                self.assertGateFailsWith(
                    summarize(records), 1, f"exactly one {event!r}; found 0"
                )
            with self.subTest(event=event, mutation="duplicate"):
                records = valid_capture(1)
                records.append(copy.deepcopy(event_named(records, event)))
                self.assertGateFailsWith(
                    summarize(records), 1, f"exactly one {event!r}; found 2"
                )

    def test_rejects_required_lifecycle_timestamp_reordering(self):
        records = valid_capture(1)
        event_named(records, "capture-ready")["monotonic_us"] = 1_000_035
        summary = summarize(records)

        self.assertGateFailsWith(summary, 1, "not in monotonic order")
        self.assertGateFailsWith(summary, 1, "decreases in journal order")

    def test_rejects_reversed_journal_order_at_equal_timestamps(self):
        records = valid_capture(1)
        for record in records:
            record["monotonic_us"] = 1_000_000
        records[1], records[2] = records[2], records[1]

        self.assertGateFailsWith(
            summarize(records), 1, "journal event order does not match"
        )

    def test_overlay_cancel_requires_both_escape_reasons(self):
        for event in ("cancelled", "overlay-closed"):
            with self.subTest(event=event):
                records = valid_capture(1)
                event_named(records, event)["reason"] = "other"
                self.assertGateFailsWith(
                    summarize(records), 1, f"{event} reason must equal 'escape'"
                )

    def test_overlay_cancel_rejects_unexpected_selection_event(self):
        records = valid_capture(1)
        records.insert(3, bridge_event("selection-started", 1, 1_000_035))

        self.assertGateFailsWith(
            summarize(records), 1, "unexpected event 'selection-started'"
        )

    def test_full_handoff_rejects_each_missing_mode_event(self):
        mode_events = MODULE.MODE_SEQUENCES["full-handoff"][3:]
        for event in mode_events:
            with self.subTest(event=event):
                records = [
                    record
                    for record in valid_capture(1, "full-handoff")
                    if record["event"] != event
                ]
                self.assertGateFailsWith(
                    summarize(records, "full-handoff"),
                    1,
                    f"exactly one {event!r}; found 0",
                )

    def test_full_handoff_rejects_reordering_and_wrong_close_reason(self):
        records = valid_capture(1, "full-handoff")
        event_named(records, "handoff-started")["monotonic_us"] = 1_000_075
        event_named(records, "overlay-closed")["reason"] = "escape"
        summary = summarize(records, "full-handoff")

        self.assertGateFailsWith(summary, 1, "not in monotonic order")
        self.assertGateFailsWith(
            summary, 1, "overlay-closed reason must equal 'commit-accepted'"
        )

    def test_rejects_refusal_failure_discard_and_indeterminate_events(self):
        for event in (
            "trigger-refused",
            "capture-failed",
            "capture-discarded",
            "handoff-refused",
            "handoff-failed",
            "commit-ack-indeterminate",
            "commit-window-lost",
        ):
            with self.subTest(event=event):
                records = valid_capture(1)
                records.append(bridge_event(event, 1, 1_000_045))
                self.assertGateFailsWith(
                    summarize(records), 1, f"rejected event {event!r}"
                )

    def test_rejects_global_bridge_events_inside_cohort(self):
        for event in MODULE.GLOBAL_EVENTS:
            with self.subTest(event=event):
                records = valid_capture(1)
                records.append(bridge_event(event, monotonic_us=1_000_025))
                self.assertGateFailsWith(
                    summarize(records), 1, f"rejected global event {event!r}"
                )

    def test_rejects_global_bridge_events_anywhere_in_exported_window(self):
        records = [
            bridge_event("enabled", monotonic_us=1),
            *valid_capture(1),
            bridge_event("disabled", monotonic_us=9_000_000),
        ]
        summary = summarize(records)

        self.assertGateFailsWith(summary, 1, "rejected global event 'enabled'")
        self.assertGateFailsWith(summary, 1, "rejected global event 'disabled'")

    def test_rejects_bridge_critical_or_error_lines(self):
        text = marked_lines(valid_capture(1))
        text = text.replace("GNOME Shell-Message:", "GNOME Shell ERROR:", 1)
        summary = MODULE.analyze(
            MODULE.read_log(io.StringIO(text), "journal"),
            STAGE,
            3,
            "overlay-cancel",
        )

        self.assertGateFailsWith(summary, 1, "bridge critical/error journal line")

    def test_detects_ready_paint_and_auxiliary_topology_mismatch(self):
        overlay = valid_capture(1)
        event_named(overlay, "first-paint")["topology_id"] = "v1-other"
        self.assertGateFailsWith(summarize(overlay), 1, "topology mismatch")

        full = valid_capture(1, "full-handoff")
        event_named(full, "selection")["topology_id"] = "v1-other"
        self.assertGateFailsWith(
            summarize(full, "full-handoff"), 1, "topology mismatch"
        )

    def test_requires_topology_on_every_topology_bound_event(self):
        for mode, event in (
            ("overlay-cancel", "first-paint"),
            ("full-handoff", "first-paint"),
            ("full-handoff", "selection"),
            ("full-handoff", "handoff-started"),
            ("full-handoff", "handoff-encoded"),
        ):
            with self.subTest(mode=mode, event=event):
                records = valid_capture(1, mode)
                event_named(records, event).pop("topology_id", None)
                self.assertGateFailsWith(
                    summarize(records, mode), 1, f"{event} topology_id"
                )

    def test_detects_topology_change_across_cohort(self):
        records = valid_capture(1) + valid_capture(2)
        for record in records:
            if record["capture_id"] == 2 and "topology_id" in record:
                record["topology_id"] = "v1-changed"
        summary = summarize(records)

        self.assertGateFailsWith(summary, 2, "cohort topology mismatch")

    def test_detects_expected_stage_mismatch_on_ready_and_paint(self):
        for event in ("capture-ready", "first-paint"):
            with self.subTest(event=event):
                records = valid_capture(1)
                event_named(records, event)["stage_width"] -= 1
                self.assertGateFailsWith(
                    summarize(records), 1, f"{event} stage"
                )

    def test_detects_expected_monitor_count_mismatch(self):
        records = valid_capture(1)
        event_named(records, "capture-ready")["monitors"] = 2

        self.assertGateFailsWith(summarize(records), 1, "reports 2 monitors")

    def test_rejects_invalid_stage_topology_and_elapsed_fields(self):
        mutations = (
            ("capture-ready", "stage_height", False, "stage_height"),
            ("capture-ready", "topology_id", "", "topology_id"),
            ("first-paint", "topology_id", None, "topology_id"),
            ("first-paint", "elapsed_us", False, "elapsed_us"),
            ("first-paint", "elapsed_us", -1, "elapsed_us"),
        )
        for event, field, value, pattern in mutations:
            with self.subTest(event=event, field=field, value=value):
                records = valid_capture(1)
                event_named(records, event)[field] = value
                self.assertGateFailsWith(summarize(records), 1, pattern)

    def test_argument_parsers_reject_unsafe_values(self):
        for value in ("0", "-1", "no"):
            with self.subTest(kind="count", value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    MODULE._positive_int_argument(value)
        for value in ("nan", "inf", "-0.1"):
            with self.subTest(kind="latency", value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    MODULE._finite_nonnegative_float(value)
        for value in ("11520", "0x2160", "11520x0", "1.5x2"):
            with self.subTest(kind="stage", value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    MODULE._stage_argument(value)
        self.assertEqual(MODULE._stage_argument("11520x2160"), STAGE)

    def test_json_cli_reports_pass_and_gate_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bridge.log"
            path.write_text(marked_lines(valid_capture(1)), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                passed = MODULE.main(
                    [
                        str(path),
                        "--expected-captures",
                        "1",
                        "--mode",
                        "overlay-cancel",
                        "--expected-monitors",
                        "3",
                        "--expected-stage",
                        "11520x2160",
                        "--json",
                    ]
                )
            result = json.loads(stdout.getvalue())

            self.assertEqual(passed, 0)
            self.assertTrue(result["gate"]["passed"])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                failed = MODULE.main(
                    [
                        str(path),
                        "--expected-captures",
                        "2",
                        "--mode",
                        "overlay-cancel",
                        "--expected-monitors",
                        "3",
                        "--expected-stage",
                        "11520x2160",
                        "--json",
                    ]
                )
            self.assertEqual(failed, 2)
            self.assertFalse(json.loads(stdout.getvalue())["gate"]["passed"])

    def test_cli_returns_input_error_for_malformed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.log"
            path.write_text(f"{MODULE.MARKER}{{broken\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = MODULE.main(
                    [
                        str(path),
                        "--mode",
                        "overlay-cancel",
                        "--expected-monitors",
                        "3",
                        "--expected-stage",
                        "11520x2160",
                    ]
                )

        self.assertEqual(result, 1)
        self.assertIn("malformed marker JSON", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
