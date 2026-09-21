#!/usr/bin/env python3

import argparse
import importlib.util
import io
import json
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "analyze-capture-trace.py"
if not SCRIPT.is_file():
    raise unittest.SkipTest("scripts/ is not part of this tree")
SPEC = importlib.util.spec_from_file_location("capture_trace_summary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def record(capture_id, event, elapsed_us, run_id=None, fields=None):
    value = {
        "schema": MODULE.TRACE_SCHEMA,
        "capture_id": capture_id,
        "event": event,
        "elapsed_us": elapsed_us,
    }
    if run_id is not None:
        value["run_id"] = run_id
    if fields is not None:
        value["fields"] = fields
    return json.dumps(value)


class CaptureTraceSummaryTest(unittest.TestCase):
    def test_default_gate_requires_thirty_captures(self):
        self.assertEqual(MODULE.DEFAULT_MIN_CAPTURES, 30)

    def test_summarizes_nearest_rank_percentiles(self):
        lines = []
        for capture_id in range(1, 21):
            lines.append(record(capture_id, "capture_requested", 0))
            lines.append(
                record(capture_id, MODULE.DEFAULT_TARGET_EVENT, capture_id * 1000)
            )
            lines.append(record(capture_id, "capture_cancelled", capture_id * 1000 + 1))
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(summary["captures"], 20)
        self.assertEqual(summary["successful_captures"], 20)
        self.assertEqual(summary["incomplete_captures"], 0)
        self.assertEqual(summary["inconsistent_captures"], 0)
        self.assertEqual(summary["target"]["p50_ms"], 10)
        self.assertEqual(summary["target"]["p95_ms"], 19)
        self.assertEqual(summary["target"]["max_ms"], 20)
        self.assertEqual(summary["target"]["samples"], 20)
        self.assertEqual(summary["target"]["raw_samples"], 20)
        self.assertEqual(summary["target"]["duplicate_samples"], 0)

    def test_counts_terminal_failure_without_success(self):
        records = MODULE.read_records(
            [
                (
                    "test",
                    io.StringIO(
                        "\n".join(
                            [
                                record(7, "capture_requested", 0),
                                record(7, "capture_failed", 500),
                            ]
                        )
                    ),
                )
            ]
        )
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(summary["captures"], 1)
        self.assertEqual(summary["successful_captures"], 0)
        self.assertEqual(summary["failed_or_rejected_captures"], 1)
        self.assertIsNone(summary["target"])

    def test_reports_duplicate_backend_events_without_weighting_percentiles(self):
        duplicate = "\n".join(
            [
                record(3, "capture_requested", 0),
                record(3, "portal_response_received", 100),
                record(3, "portal_response_received", 101),
            ]
        )
        records = MODULE.read_records([("test", io.StringIO(duplicate))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(summary["captures"], 1)
        event = summary["events"]["portal_response_received"]
        self.assertEqual(event["samples"], 1)
        self.assertEqual(event["raw_samples"], 2)
        self.assertEqual(event["duplicate_samples"], 1)
        self.assertEqual(event["duplicate_captures"], 1)
        self.assertEqual(event["p95_ms"], 0.1)
        self.assertEqual(summary["duplicate_event_records"], 1)

    def test_target_percentile_uses_one_sample_per_capture(self):
        lines = [record(1, "capture_requested", 0)]
        lines.extend(
            record(1, MODULE.DEFAULT_TARGET_EVENT, elapsed_us)
            for elapsed_us in range(100, 200)
        )
        lines.extend(
            [
                record(2, "capture_requested", 0),
                record(2, MODULE.DEFAULT_TARGET_EVENT, 10_000),
                record(2, "capture_cancelled", 10_001),
            ]
        )
        lines.append(record(1, "capture_cancelled", 200))
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(summary["target"]["samples"], 2)
        self.assertEqual(summary["target"]["raw_samples"], 101)
        self.assertEqual(summary["target"]["duplicate_samples"], 99)
        self.assertEqual(summary["target"]["p50_ms"], 0.1)
        self.assertEqual(summary["target"]["p95_ms"], 10)

    def test_gate_rejects_one_success_in_thirty_captures(self):
        lines = []
        for capture_id in range(1, 31):
            lines.append(record(capture_id, "capture_requested", 0))
        lines.append(record(1, MODULE.DEFAULT_TARGET_EVENT, 10_000))
        lines.append(record(1, "capture_cancelled", 10_001))
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        failures = MODULE.evaluate_gate(summary, 150, 30)
        self.assertEqual(summary["successful_captures"], 1)
        self.assertEqual(summary["incomplete_captures"], 29)
        self.assertTrue(any("29 captures are incomplete" in item for item in failures))

    def test_gate_accepts_complete_thirty_capture_cohort(self):
        lines = []
        for capture_id in range(1, 31):
            lines.extend(
                [
                    record(capture_id, "capture_requested", 0),
                    record(capture_id, MODULE.DEFAULT_TARGET_EVENT, 100_000),
                    record(capture_id, "capture_cancelled", 100_001),
                ]
            )
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(MODULE.evaluate_gate(summary, 150, 30), [])

    def test_gate_rejects_failed_and_inconsistent_lifecycles(self):
        lines = [
            record(1, "capture_requested", 0),
            record(1, "capture_failed", 100),
            record(2, "capture_requested", 0),
            record(2, MODULE.DEFAULT_TARGET_EVENT, 100),
            record(2, "capture_failed", 200),
            record(3, MODULE.DEFAULT_TARGET_EVENT, 100),
            record(3, "capture_cancelled", 200),
        ]
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)
        failures = MODULE.evaluate_gate(summary, 150, 1)

        self.assertEqual(summary["failed_or_rejected_captures"], 1)
        self.assertEqual(summary["inconsistent_captures"], 2)
        self.assertTrue(any("failed or were rejected" in item for item in failures))
        self.assertTrue(any("inconsistent lifecycles" in item for item in failures))

    def test_gate_rejects_duplicate_start_and_out_of_order_terminal(self):
        lines = [
            record(1, "capture_requested", 0),
            record(1, "capture_requested", 1),
            record(1, MODULE.DEFAULT_TARGET_EVENT, 100),
            record(1, "capture_cancelled", 101),
            record(2, "capture_requested", 0),
            record(2, MODULE.DEFAULT_TARGET_EVENT, 100),
            record(2, "capture_cancelled", 50),
        ]
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        self.assertEqual(summary["inconsistent_captures"], 2)
        failures = MODULE.evaluate_gate(summary, 150, 1)
        self.assertTrue(any("inconsistent lifecycles" in item for item in failures))

    def test_gate_rejects_mixed_sources_and_origins(self):
        lines = []
        for capture_id, source, origin in (
            (1, "cli_graphical", "process_start"),
            (2, "request_capture", "request_receipt"),
        ):
            lines.extend(
                [
                    record(
                        capture_id,
                        "capture_requested",
                        0,
                        fields={"source": source, "origin": origin},
                    ),
                    record(capture_id, MODULE.DEFAULT_TARGET_EVENT, 100),
                    record(capture_id, "capture_cancelled", 101),
                ]
            )
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])
        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)
        failures = MODULE.evaluate_gate(summary, 150, 1)

        self.assertEqual(summary["sources"], {"cli_graphical": 1, "request_capture": 1})
        self.assertEqual(summary["origins"], {"process_start": 1, "request_receipt": 1})
        self.assertTrue(any("mixes sources" in item for item in failures))
        self.assertTrue(any("mixes timeline origins" in item for item in failures))

    def test_reads_prefixed_stderr_trace_and_ignores_diagnostics(self):
        trace_line = record(1, "capture_requested", 0)
        stream = io.StringIO(
            f"{MODULE.STDERR_PREFIX}{trace_line}\n"
            "snipsnap: info: Screenshot aborted.\n"
        )
        records = MODULE.read_records([("stderr", stream)])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "capture_requested")

    def test_threshold_parser_rejects_nonfinite_and_negative_values(self):
        for value in ("nan", "inf", "-inf", "-0.001"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    MODULE._finite_nonnegative_float(value)
        self.assertEqual(MODULE._finite_nonnegative_float("0"), 0)
        self.assertEqual(MODULE._finite_nonnegative_float("150.5"), 150.5)

    def test_run_id_filter_prevents_cross_run_contamination(self):
        lines = [
            record(1, "capture_requested", 0, "run-a"),
            record(1, MODULE.DEFAULT_TARGET_EVENT, 100, "run-a"),
            record(1, "capture_cancelled", 101, "run-a"),
            record(1, "capture_requested", 0, "run-b"),
            record(1, MODULE.DEFAULT_TARGET_EVENT, 10_000, "run-b"),
            record(1, "capture_cancelled", 10_001, "run-b"),
        ]
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])

        with self.assertRaisesRegex(MODULE.TraceError, "multiple trace run IDs"):
            MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        summary = MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT, "run-b")
        self.assertEqual(summary["run_id"], "run-b")
        self.assertEqual(summary["captures"], 1)
        self.assertEqual(summary["target"]["p95_ms"], 10)
        self.assertEqual(summary["available_run_ids"], ["run-a", "run-b"])

    def test_legacy_and_new_run_ids_require_explicit_selection(self):
        lines = [
            record(1, "capture_requested", 0),
            record(1, MODULE.DEFAULT_TARGET_EVENT, 100),
            record(1, "capture_cancelled", 101),
            record(2, "capture_requested", 0, "run-a"),
            record(2, MODULE.DEFAULT_TARGET_EVENT, 200, "run-a"),
            record(2, "capture_cancelled", 201, "run-a"),
        ]
        records = MODULE.read_records([("test", io.StringIO("\n".join(lines)))])

        with self.assertRaisesRegex(MODULE.TraceError, "multiple trace run IDs"):
            MODULE.summarize(records, MODULE.DEFAULT_TARGET_EVENT)

        legacy = MODULE.summarize(
            records, MODULE.DEFAULT_TARGET_EVENT, MODULE.LEGACY_RUN_ID
        )
        self.assertEqual(legacy["run_id"], MODULE.LEGACY_RUN_ID)
        self.assertEqual(legacy["captures"], 1)

    def test_rejects_invalid_run_id(self):
        for run_id in ("", 7, MODULE.LEGACY_RUN_ID):
            value = {
                "schema": MODULE.TRACE_SCHEMA,
                "capture_id": 1,
                "event": "capture_requested",
                "elapsed_us": 0,
                "run_id": run_id,
            }
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(MODULE.TraceError, "run_id"):
                    MODULE.read_records([("test", io.StringIO(json.dumps(value)))])

    def test_rejects_sensitive_or_unknown_schema(self):
        wrong = json.dumps(
            {
                "schema": "something-else",
                "capture_id": 1,
                "event": "capture_requested",
                "elapsed_us": 0,
            }
        )
        with self.assertRaisesRegex(MODULE.TraceError, "unsupported trace schema"):
            MODULE.read_records([("test", io.StringIO(wrong))])


if __name__ == "__main__":
    unittest.main()
