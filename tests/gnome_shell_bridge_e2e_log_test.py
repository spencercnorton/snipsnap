#!/usr/bin/env python3

import copy
import json
import unittest

import gnome_shell_bridge_e2e_log as evidence


def valid_records():
    capture_id = 7
    daemon_pid = 4242
    topology_id = "v1-0d9a37d2"
    monitor_layout = [
        {"index": 0, "x": 0, "y": 0, "width": 1280, "height": 720, "scale": 1},
        {
            "index": 1,
            "x": 1280,
            "y": 0,
            "width": 1280,
            "height": 720,
            "scale": 1,
        },
        {
            "index": 2,
            "x": 2560,
            "y": 0,
            "width": 1280,
            "height": 720,
            "scale": 1,
        },
    ]
    events = [
        {"event": "enabled", "monotonic_us": 1_000_000},
        {"event": "trigger", "capture_id": capture_id, "monotonic_us": 1_001_000},
        {
            "event": "capture-ready",
            "capture_id": capture_id,
            "monitors": 3,
            "stage_width": 3840,
            "stage_height": 720,
            "scale": 1,
            "capture_scale": 1,
            "capture_layout_mode": "scale-one",
            "stage_view_scales": [1, 1, 1],
            "texture_width": 3840,
            "texture_height": 720,
            "topology_id": topology_id,
            "topology_generation": 3,
            "monitor_layout": monitor_layout,
            "elapsed_us": 12_000,
            "monotonic_us": 1_013_000,
        },
        {
            "event": "first-paint",
            "capture_id": capture_id,
            "stage_width": 3840,
            "stage_height": 720,
            "topology_id": topology_id,
            "elapsed_us": 20_000,
            "monotonic_us": 1_021_000,
        },
        {
            "event": "selection",
            "capture_id": capture_id,
            "x": 64,
            "y": 140,
            "width": 3713,
            "height": 381,
            "scale": 1,
            "topology_id": topology_id,
            "elapsed_us": 600_000,
            "monotonic_us": 1_601_000,
        },
        {
            "event": "handoff-started",
            "capture_id": capture_id,
            "x": 64,
            "y": 140,
            "width": 3713,
            "height": 381,
            "scale": 1,
            "topology_id": topology_id,
            "elapsed_us": 700_000,
            "monotonic_us": 1_701_000,
        },
        {
            "event": "handoff-encoded",
            "capture_id": capture_id,
            "pixel_width": 3713,
            "pixel_height": 381,
            "png_bytes": 23_998,
            "topology_id": topology_id,
            "elapsed_us": 720_000,
            "monotonic_us": 1_721_000,
        },
        {
            "event": "editor-window-observed",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "title": f"SnipSnap [capture-id={capture_id}]",
            "monotonic_us": 1_741_000,
        },
        {
            "event": "commit-accepted",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "elapsed_us": 760_000,
            "monotonic_us": 1_761_000,
        },
        {
            "event": "overlay-closed",
            "capture_id": capture_id,
            "reason": "commit-accepted",
            "monotonic_us": 1_762_000,
        },
        {
            "event": "editor-focus-requested",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "monotonic_us": 1_763_000,
        },
    ]
    for event in events:
        event["component"] = "snipsnap-shell-bridge"
    result = {
        "event": "passed",
        "capture_id": capture_id,
        "daemon_pid": daemon_pid,
        "monitors": 3,
        "stage_width": 3840,
        "stage_height": 720,
        "monitor_layout": monitor_layout,
        "selection": {"x": 64, "y": 140, "width": 3713, "height": 381},
        "selection_spans_monitors": 3,
        "editor_normal": True,
        "editor_focused": True,
        "editor_showing": True,
        "editor_mapped": True,
        "overlay_closed": True,
        "editor_title": f"SnipSnap [capture-id={capture_id}]",
        "identities": ["tech.norvi.snipsnap"],
        "overlay_screenshot": "/tmp/snipsnap-e2e/overlay.png",
        "editor_screenshot": "/tmp/snipsnap-e2e/editor.png",
        "editor_one_to_one_screenshot": (
            "/tmp/snipsnap-e2e/editor-one-to-one.png"
        ),
        "editor_frame": {"x": 0, "y": 32, "width": 1280, "height": 688},
    }
    return events, result


def synchronize_timed_monotonic(events):
    trigger_us = events[1]["monotonic_us"]
    by_name = {event["event"]: event for event in events}
    for name in evidence.TIMED_EVENTS:
        event = by_name[name]
        event["monotonic_us"] = trigger_us + event["elapsed_us"]
    encoded_us = by_name["handoff-encoded"]["monotonic_us"]
    committed_us = by_name["commit-accepted"]["monotonic_us"]
    by_name["editor-window-observed"]["monotonic_us"] = (
        encoded_us + (committed_us - encoded_us) // 2
    )
    by_name["overlay-closed"]["monotonic_us"] = committed_us + 1_000
    by_name["editor-focus-requested"]["monotonic_us"] = committed_us + 2_000


def valid_traces(events):
    capture_id = events[1]["capture_id"]
    encoded = next(event for event in events if event["event"] == "handoff-encoded")
    selection = next(event for event in events if event["event"] == "selection")
    native_capture_id = 123456
    view_scale = min(1280 / selection["width"], 720 / selection["height"], 1)
    return [
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "capture_requested",
            "elapsed_us": 0,
            "fields": {
                "source": "gnome_shell_bridge",
                "origin": "request_receipt",
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "shell_bridge_region_received",
            "elapsed_us": 1_000,
            "fields": {
                "bridge_capture_id": str(capture_id),
                "encoded_bytes": encoded["png_bytes"],
                "pixel_width": encoded["pixel_width"],
                "pixel_height": encoded["pixel_height"],
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "capture_widget_show_requested",
            "elapsed_us": 10_000,
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "capture_widget_paint_completed",
            "elapsed_us": 15_000,
            "fields": {
                "width": selection["width"],
                "height": selection["height"],
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "region_editor_view_configured",
            "elapsed_us": 16_000,
            "fields": {
                "fit_mode": "whole_canvas",
                "canvas_width": selection["width"],
                "canvas_height": selection["height"],
                "output_bound_width": 1280,
                "output_bound_height": 720,
                "window_width": 1280,
                "window_height": 688,
                "viewport_width": 1280,
                "viewport_height": 688,
                "view_scale_ppm": round(view_scale * 1_000_000),
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "editor_ready",
            "elapsed_us": 20_000,
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "editor_committed",
            "elapsed_us": 30_000,
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "region_editor_view_changed",
            "elapsed_us": 31_000,
            "fields": {
                "mode": "one_to_one",
                "view_scale_ppm": 1_000_000,
                "viewport_width": 1280,
                "viewport_height": 688,
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "region_editor_view_panned",
            "elapsed_us": 32_000,
            "fields": {
                "view_scale_ppm": 1_000_000,
                "offset_delta_x": 120,
                "offset_delta_y": 0,
                "viewport_width": 1280,
                "viewport_height": 688,
            },
        },
        {
            "schema": "snipsnap.capture-trace.v1",
            "run_id": "gnome-shell-bridge-e2e",
            "capture_id": native_capture_id,
            "event": "region_editor_view_changed",
            "elapsed_us": 33_000,
            "fields": {
                "mode": "whole_canvas",
                "view_scale_ppm": round(view_scale * 1_000_000),
                "viewport_width": 1280,
                "viewport_height": 688,
            },
        },
    ]


def render(
    events,
    result,
    traces=None,
    *,
    pass_first=False,
    navigation_after_pass=False,
):
    bridge_lines = [
        f"GNOME Shell-Message: [snipsnap-shell-bridge] {json.dumps(event)}"
        for event in events
    ]
    trace_lines = [
        f"SNIPSNAP_CAPTURE_TRACE {json.dumps(trace)}"
        for trace in (valid_traces(events) if traces is None else traces)
    ]
    pass_line = f"GNOME Shell-Message: E2E-PASS {json.dumps(result)}"
    if pass_first:
        lines = [pass_line, *bridge_lines, *trace_lines]
    elif navigation_after_pass:
        navigation_markers = (
            '"event": "region_editor_view_changed"',
            '"event": "region_editor_view_panned"',
        )
        navigation_lines = [
            line for line in trace_lines if any(mark in line for mark in navigation_markers)
        ]
        core_trace_lines = [line for line in trace_lines if line not in navigation_lines]
        lines = [*bridge_lines, *core_trace_lines, pass_line, *navigation_lines]
    else:
        lines = [*bridge_lines, *trace_lines, pass_line]
    return "\n".join(lines)


class GnomeShellBridgeEvidenceTest(unittest.TestCase):
    def test_accepts_complete_three_monitor_lifecycle(self):
        events, result = valid_records()

        summary = evidence.validate_log(render(events, result))

        self.assertEqual(summary["capture_id"], 7)
        self.assertEqual(summary["monitors"], 3)
        self.assertEqual(summary["selection_width"], 3713)
        self.assertEqual(summary["png_bytes"], 23_998)
        self.assertEqual(summary["handoff_encode_us"], 20_000)
        self.assertEqual(summary["handoff_post_encode_us"], 40_000)
        self.assertEqual(summary["handoff_us"], 60_000)
        self.assertEqual(summary["topology_id"], "v1-0d9a37d2")
        self.assertEqual(summary["topology_generation"], 3)

    def test_rejects_out_of_order_commit(self):
        events, result = valid_records()
        events[-3], events[-2] = events[-2], events[-3]
        events[-3]["monotonic_us"] = 1_760_000
        events[-2]["monotonic_us"] = 1_761_000

        with self.assertRaisesRegex(evidence.EvidenceError, "out of order"):
            evidence.validate_log(render(events, result))

    def test_rejects_mismatched_capture_id(self):
        events, result = valid_records()
        events[6]["capture_id"] = 99

        with self.assertRaisesRegex(evidence.EvidenceError, "mismatched capture_id"):
            evidence.validate_log(render(events, result))

    def test_rejects_forbidden_bridge_event(self):
        events, result = valid_records()
        events.insert(
            -1,
            {
                "component": "snipsnap-shell-bridge",
                "event": "handoff-failed",
                "monotonic_us": 1_762_500,
            },
        )

        with self.assertRaisesRegex(evidence.EvidenceError, "forbidden bridge event"):
            evidence.validate_log(render(events, result))

    def test_rejects_duplicate_required_event(self):
        events, result = valid_records()
        events.insert(3, copy.deepcopy(events[2]))

        with self.assertRaisesRegex(
            evidence.EvidenceError, "exactly one 'capture-ready'"
        ):
            evidence.validate_log(render(events, result))

    def test_rejects_a_mutter_critical_even_when_lifecycle_completes(self):
        events, result = valid_records()
        log = render(events, result) + "\nlibmutter-CRITICAL stack assertion"

        with self.assertRaisesRegex(evidence.EvidenceError, "CRITICAL"):
            evidence.validate_log(log)

    def test_rejects_selection_that_does_not_span_all_monitors(self):
        events, result = valid_records()
        result["selection_spans_monitors"] = 2

        with self.assertRaisesRegex(evidence.EvidenceError, "all three monitors"):
            evidence.validate_log(render(events, result))

    def test_rejects_malformed_structured_event(self):
        events, result = valid_records()
        log = render(events, result) + "\n[snipsnap-shell-bridge] {not-json}"

        with self.assertRaisesRegex(evidence.EvidenceError, "malformed JSON"):
            evidence.validate_log(log)

    def test_rejects_decreasing_monotonic_timestamp(self):
        events, result = valid_records()
        events[5]["monotonic_us"] = events[4]["monotonic_us"] - 1

        with self.assertRaisesRegex(
            evidence.EvidenceError, "monotonic_us values decrease"
        ):
            evidence.validate_log(render(events, result))

    def test_rejects_negative_elapsed_timestamp(self):
        events, result = valid_records()
        events[4]["elapsed_us"] = -1

        with self.assertRaisesRegex(evidence.EvidenceError, "selection elapsed_us"):
            evidence.validate_log(render(events, result))

    def test_rejects_slow_capture_ready(self):
        events, result = valid_records()
        events[2]["elapsed_us"] = evidence.CAPTURE_READY_BUDGET_US + 1
        events[3]["elapsed_us"] = events[2]["elapsed_us"]
        synchronize_timed_monotonic(events)

        with self.assertRaisesRegex(evidence.EvidenceError, "capture-ready exceeded"):
            evidence.validate_log(render(events, result))

    def test_rejects_slow_selected_region_handoff(self):
        events, result = valid_records()
        events[6]["elapsed_us"] = events[5]["elapsed_us"] + 125_000
        events[8]["elapsed_us"] = (
            events[5]["elapsed_us"]
            + evidence.HEADLESS_HANDOFF_SANITY_BUDGET_US
            + 1
        )
        synchronize_timed_monotonic(events)

        with self.assertRaisesRegex(evidence.EvidenceError, "handoff exceeded"):
            evidence.validate_log(render(events, result))

    def test_headless_handoff_sanity_budgets_are_inclusive(self):
        cases = (
            (evidence.HEADLESS_ENCODE_SANITY_BUDGET_US, 100_000),
            (100_000, evidence.HEADLESS_POST_ENCODE_SANITY_BUDGET_US),
        )
        for encode_us, post_encode_us in cases:
            with self.subTest(
                encode_us=encode_us, post_encode_us=post_encode_us
            ):
                events, result = valid_records()
                events[6]["elapsed_us"] = events[5]["elapsed_us"] + encode_us
                events[8]["elapsed_us"] = (
                    events[6]["elapsed_us"] + post_encode_us
                )
                synchronize_timed_monotonic(events)

                summary = evidence.validate_log(render(events, result))

                self.assertEqual(summary["handoff_encode_us"], encode_us)
                self.assertEqual(
                    summary["handoff_post_encode_us"], post_encode_us
                )
                self.assertEqual(
                    summary["handoff_us"],
                    evidence.HEADLESS_HANDOFF_SANITY_BUDGET_US,
                )

    def test_rejects_slow_encode_within_gross_handoff_budget(self):
        events, result = valid_records()
        events[6]["elapsed_us"] = (
            events[5]["elapsed_us"]
            + evidence.HEADLESS_ENCODE_SANITY_BUDGET_US
            + 1
        )
        events[8]["elapsed_us"] = events[6]["elapsed_us"] + 1
        synchronize_timed_monotonic(events)

        with self.assertRaisesRegex(evidence.EvidenceError, "encode exceeded"):
            evidence.validate_log(render(events, result))

    def test_rejects_slow_post_encode_within_gross_handoff_budget(self):
        events, result = valid_records()
        events[6]["elapsed_us"] = events[5]["elapsed_us"] + 1
        events[8]["elapsed_us"] = (
            events[6]["elapsed_us"]
            + evidence.HEADLESS_POST_ENCODE_SANITY_BUDGET_US
            + 1
        )
        synchronize_timed_monotonic(events)

        with self.assertRaisesRegex(
            evidence.EvidenceError, "post-encode handoff exceeded"
        ):
            evidence.validate_log(render(events, result))

    def test_prehandoff_delay_does_not_change_handoff_metric(self):
        events, result = valid_records()
        events[4]["elapsed_us"] = events[3]["elapsed_us"] + 1
        synchronize_timed_monotonic(events)

        summary = evidence.validate_log(render(events, result))

        self.assertEqual(summary["handoff_us"], 60_000)

    def test_rejects_elapsed_and_journal_clock_mismatch(self):
        events, result = valid_records()
        for event in events[8:]:
            event["monotonic_us"] += 60_000_000

        with self.assertRaisesRegex(
            evidence.EvidenceError, "elapsed_us differs from the journal"
        ):
            evidence.validate_log(render(events, result))

    def test_rejects_zero_duration_handoff_phases(self):
        for phase in ("encode", "post-encode"):
            with self.subTest(phase=phase):
                events, result = valid_records()
                if phase == "encode":
                    events[6]["elapsed_us"] = events[5]["elapsed_us"]
                else:
                    events[8]["elapsed_us"] = events[6]["elapsed_us"]
                synchronize_timed_monotonic(events)

                with self.assertRaisesRegex(
                    evidence.EvidenceError, f"{phase}.*duration must be positive"
                ):
                    evidence.validate_log(render(events, result))

    def test_rejects_budget_straddling_between_evidence_clocks(self):
        trigger_us = valid_records()[0][1]["monotonic_us"]
        cases = (
            ("first-paint", "first-paint exceeded"),
            ("encode", "encode exceeded"),
            ("post-encode", "post-encode handoff exceeded"),
            ("gross", "selected-region handoff exceeded"),
        )
        for case, expected_error in cases:
            with self.subTest(case=case):
                events, result = valid_records()
                if case == "first-paint":
                    events[3]["elapsed_us"] = 110_000
                    events[3]["monotonic_us"] = trigger_us + 100_000
                elif case == "encode":
                    events[5]["elapsed_us"] = 700_000
                    events[5]["monotonic_us"] = trigger_us + 710_000
                    events[6]["elapsed_us"] = 860_000
                    events[6]["monotonic_us"] = trigger_us + 860_000
                    events[8]["elapsed_us"] = 861_000
                    events[8]["monotonic_us"] = trigger_us + 861_000
                elif case == "post-encode":
                    events[6]["elapsed_us"] = 720_000
                    events[6]["monotonic_us"] = trigger_us + 720_000
                    events[8]["elapsed_us"] = 880_000
                    events[8]["monotonic_us"] = trigger_us + 870_000
                else:
                    events[5]["elapsed_us"] = 700_000
                    events[5]["monotonic_us"] = trigger_us + 710_000
                    events[6]["elapsed_us"] = 835_000
                    events[6]["monotonic_us"] = trigger_us + 835_000
                    events[8]["elapsed_us"] = 970_000
                    events[8]["monotonic_us"] = trigger_us + 960_000

                encoded_us = events[6]["monotonic_us"]
                committed_us = events[8]["monotonic_us"]
                events[7]["monotonic_us"] = (
                    encoded_us + (committed_us - encoded_us) // 2
                )
                events[9]["monotonic_us"] = committed_us + 1_000
                events[10]["monotonic_us"] = committed_us + 2_000

                with self.assertRaisesRegex(
                    evidence.EvidenceError, expected_error
                ):
                    evidence.validate_log(render(events, result))

    def test_rejects_handoff_geometry_that_differs_from_selection(self):
        events, result = valid_records()
        events[5]["width"] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "handoff-started geometry"):
            evidence.validate_log(render(events, result))

    def test_rejects_full_stage_pixels_for_selected_region(self):
        events, result = valid_records()
        events[6]["pixel_width"] = 3840
        events[6]["pixel_height"] = 720

        with self.assertRaisesRegex(evidence.EvidenceError, "encoded pixel dimensions"):
            evidence.validate_log(render(events, result))

    def test_rejects_overlay_close_without_bound_capture_id(self):
        events, result = valid_records()
        del events[9]["capture_id"]

        with self.assertRaisesRegex(
            evidence.EvidenceError, "overlay-closed.*capture_id"
        ):
            evidence.validate_log(render(events, result))

    def test_rejects_non_integer_stage_dimension(self):
        events, result = valid_records()
        events[2]["stage_width"] = 3840.0

        with self.assertRaisesRegex(evidence.EvidenceError, "stage_width"):
            evidence.validate_log(render(events, result))

    def test_rejects_pass_before_lifecycle(self):
        events, result = valid_records()

        with self.assertRaisesRegex(evidence.EvidenceError, "precedes"):
            evidence.validate_log(render(events, result, pass_first=True))

    def test_rejects_changed_monitor_topology(self):
        events, result = valid_records()
        result["monitor_layout"][2]["x"] = 1280

        with self.assertRaisesRegex(evidence.EvidenceError, "monitor topology"):
            evidence.validate_log(render(events, result))

    def test_rejects_capture_texture_that_differs_from_stage_contract(self):
        events, result = valid_records()
        events[2]["texture_width"] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "texture differs"):
            evidence.validate_log(render(events, result))

    def test_rejects_capture_stage_view_or_layout_mode_mutation(self):
        for field, value, message in (
            ("capture_scale", 2, "capture_scale"),
            ("stage_view_scales", [1, 1, 2], "stage_view_scales"),
            ("capture_layout_mode", "physical", "capture_layout_mode"),
        ):
            with self.subTest(field=field):
                events, result = valid_records()
                events[2][field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, result))

    def test_rejects_capture_ready_monitor_layout_mismatch(self):
        events, result = valid_records()
        events[2]["monitor_layout"][2]["x"] = 2559

        with self.assertRaisesRegex(evidence.EvidenceError, "monitor topology"):
            evidence.validate_log(render(events, result))

    def test_rejects_topology_identity_change_before_handoff(self):
        events, result = valid_records()
        events[5]["topology_id"] = "v1-deadbeef"

        with self.assertRaisesRegex(evidence.EvidenceError, "topology differs"):
            evidence.validate_log(render(events, result))

    def test_rejects_topology_hash_generation_mismatch(self):
        events, result = valid_records()
        events[2]["topology_generation"] = 4

        with self.assertRaisesRegex(evidence.EvidenceError, "exact topology generation"):
            evidence.validate_log(render(events, result))

    def test_rejects_native_receiver_geometry_mismatch(self):
        events, result = valid_records()
        traces = valid_traces(events)
        traces[1]["fields"]["pixel_width"] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "native receiver trace"):
            evidence.validate_log(render(events, result, traces))

    def test_rejects_native_canvas_or_view_mutation(self):
        for delta in (-1, 1):
            with self.subTest(paint_width_delta=delta):
                events, result = valid_records()
                traces = valid_traces(events)
                paint = next(
                    trace
                    for trace in traces
                    if trace["event"] == "capture_widget_paint_completed"
                )
                paint["fields"]["width"] += delta
                with self.assertRaisesRegex(evidence.EvidenceError, "exactly equal"):
                    evidence.validate_log(render(events, result, traces))

        events, result = valid_records()
        traces = valid_traces(events)
        view = next(
            trace
            for trace in traces
            if trace["event"] == "region_editor_view_configured"
        )
        view["fields"]["view_scale_ppm"] += 10
        with self.assertRaisesRegex(evidence.EvidenceError, "view scale"):
            evidence.validate_log(render(events, result, traces))

    def test_rejects_missing_or_false_editor_navigation(self):
        events, result = valid_records()
        traces = valid_traces(events)
        traces = [
            trace
            for trace in traces
            if trace["event"] != "region_editor_view_panned"
        ]
        with self.assertRaisesRegex(evidence.EvidenceError, "1:1, pan, and fit"):
            evidence.validate_log(render(events, result, traces))

        events, result = valid_records()
        traces = valid_traces(events)
        pan = next(
            trace
            for trace in traces
            if trace["event"] == "region_editor_view_panned"
        )
        pan["fields"].update({"offset_delta_x": 0, "offset_delta_y": 0})
        with self.assertRaisesRegex(evidence.EvidenceError, "did not move"):
            evidence.validate_log(render(events, result, traces))

    def test_rejects_navigation_after_pass(self):
        events, result = valid_records()
        with self.assertRaisesRegex(evidence.EvidenceError, "precedes completed"):
            evidence.validate_log(
                render(events, result, navigation_after_pass=True)
            )

    def test_rejects_editor_frame_or_navigation_artifact_mutation(self):
        events, result = valid_records()
        result["editor_frame"]["width"] -= 1
        with self.assertRaisesRegex(evidence.EvidenceError, "editor_frame differs"):
            evidence.validate_log(render(events, result))

        events, result = valid_records()
        result["editor_frame"].update({"x": 2560, "y": 32})
        with self.assertRaisesRegex(evidence.EvidenceError, "primary output"):
            evidence.validate_log(render(events, result))

        events, result = valid_records()
        result["editor_frame"]["x"] = -1
        with self.assertRaisesRegex(evidence.EvidenceError, "editor_frame.x"):
            evidence.validate_log(render(events, result))

        events, result = valid_records()
        result["editor_one_to_one_screenshot"] = (
            "/tmp/other/editor-one-to-one.png"
        )
        with self.assertRaisesRegex(evidence.EvidenceError, "one output directory"):
            evidence.validate_log(render(events, result))

    def test_rejects_untrusted_editor_identity(self):
        events, result = valid_records()
        result["identities"] = ["not-snipsnap"]

        with self.assertRaisesRegex(evidence.EvidenceError, "accepted editor identity"):
            evidence.validate_log(render(events, result))


if __name__ == "__main__":
    unittest.main()
