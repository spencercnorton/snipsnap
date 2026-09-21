#!/usr/bin/env python3

import copy
import json
import unittest

import gnome_shell_bridge_mixed_e2e_log as evidence


def exact_monitor_layout():
    return copy.deepcopy(evidence.EXPECTED_MONITOR_LAYOUT)


def exact_physical_monitor_layout():
    return copy.deepcopy(evidence.EXPECTED_PHYSICAL_MONITOR_LAYOUT)


def valid_physical_encoder_proof():
    return {
        "topology_id": "v1-62d492f3",
        "monitor_layout": exact_physical_monitor_layout(),
        "stage_view_scales": [1, 1, 1, 1],
        "capture_layout_mode": "physical",
        "capture_scale": 1,
        "stage_width": 3640,
        "stage_height": 1600,
        "texture_width": 3640,
        "texture_height": 1600,
        "selection": {"x": 20, "y": 20, "width": 3600, "height": 1560},
        "pixel_width": 3600,
        "pixel_height": 1560,
        "png_path": "/tmp/snipsnap-mixed-e2e/physical-selected.png",
        "landmarks": 4,
        "stage_hole_verified": True,
    }


def valid_encoder_proof():
    return {
        "topology_id": "v1-788a8fa9",
        "monitor_layout": exact_monitor_layout(),
        "stage_view_scales": [1, 1.25, 1.5, 2],
        "capture_layout_mode": "logical",
        "capture_scale": 2,
        "stage_width": 2560,
        "stage_height": 800,
        "texture_width": 5120,
        "texture_height": 1600,
        "selection": {"x": 20, "y": 20, "width": 2520, "height": 760},
        "pixel_width": 5040,
        "pixel_height": 1520,
        "png_path": "/tmp/snipsnap-mixed-e2e/mixed-selected.png",
        "landmarks": 4,
        "stage_hole_verified": True,
    }


def valid_records():
    capture_id = 1
    daemon_pid = 4242
    topology_id = "v1-fd50135f"
    layout = exact_monitor_layout()
    selection = {"x": 20, "y": 20, "width": 2521, "height": 761}
    events = [
        {"event": "enabled", "monotonic_us": 1_000_000},
        {"event": "trigger", "capture_id": capture_id, "monotonic_us": 1_001_000},
        {
            "event": "capture-ready",
            "capture_id": capture_id,
            "monitors": 4,
            "stage_width": 2560,
            "stage_height": 800,
            "scale": 2,
            "capture_scale": 2,
            "capture_layout_mode": "logical",
            "stage_view_scales": [1, 1.25, 1.5, 2],
            "texture_width": 5120,
            "texture_height": 1600,
            "topology_id": topology_id,
            "topology_generation": 7,
            "monitor_layout": layout,
            "elapsed_us": 120_000,
            "monotonic_us": 1_121_000,
        },
        {
            "event": "first-paint",
            "capture_id": capture_id,
            "stage_width": 2560,
            "stage_height": 800,
            "topology_id": topology_id,
            "elapsed_us": 140_000,
            "monotonic_us": 1_141_000,
        },
        {
            "event": "selection-started",
            "capture_id": capture_id,
            "x": 20,
            "y": 20,
            "monotonic_us": 1_501_000,
        },
        {
            "event": "selection",
            "capture_id": capture_id,
            **selection,
            "scale": 2,
            "topology_id": topology_id,
            "elapsed_us": 600_000,
            "monotonic_us": 1_601_000,
        },
        {
            "event": "handoff-started",
            "capture_id": capture_id,
            **selection,
            "scale": 2,
            "topology_id": topology_id,
            "elapsed_us": 700_000,
            "monotonic_us": 1_701_000,
        },
        {
            "event": "handoff-encoded",
            "capture_id": capture_id,
            "pixel_width": 5042,
            "pixel_height": 1522,
            "png_bytes": 76_543,
            "topology_id": topology_id,
            "elapsed_us": 760_000,
            "monotonic_us": 1_761_000,
        },
        {
            "event": "editor-window-observed",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "title": "SnipSnap [capture-id=1]",
            "monotonic_us": 1_781_000,
        },
        {
            "event": "commit-accepted",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "elapsed_us": 790_000,
            "monotonic_us": 1_791_000,
        },
        {
            "event": "overlay-closed",
            "capture_id": capture_id,
            "reason": "commit-accepted",
            "monotonic_us": 1_792_000,
        },
        {
            "event": "editor-focus-requested",
            "capture_id": capture_id,
            "daemon_pid": daemon_pid,
            "monotonic_us": 1_793_000,
        },
    ]
    for event in events:
        event["component"] = "snipsnap-shell-bridge"

    proof = valid_encoder_proof()
    result = {
        "event": "passed",
        "capture_id": capture_id,
        "daemon_pid": daemon_pid,
        "monitors": 4,
        "stage_width": 2560,
        "stage_height": 800,
        "monitor_layout": exact_monitor_layout(),
        "selection": selection,
        "selection_spans_monitors": 4,
        "physical_encoder_proof": valid_physical_encoder_proof(),
        "encoder_proof": copy.deepcopy(proof),
        "editor_normal": True,
        "editor_focused": True,
        "editor_showing": True,
        "editor_mapped": True,
        "overlay_closed": True,
        "editor_title": "SnipSnap [capture-id=1]",
        "identities": ["tech.norvi.snipsnap"],
        "overlay_screenshot": "/tmp/snipsnap-mixed-e2e/mixed-overlay.png",
        "editor_screenshot": "/tmp/snipsnap-mixed-e2e/mixed-editor.png",
        "editor_one_to_one_screenshot": (
            "/tmp/snipsnap-mixed-e2e/mixed-editor-one-to-one.png"
        ),
        "editor_frame": {"x": 0, "y": 0, "width": 480, "height": 480},
        "helper_window_removed": True,
        "editor_landmarks_verified": True,
    }
    return events, proof, result


def valid_traces():
    common = {
        "schema": "snipsnap.capture-trace.v1",
        "run_id": "gnome-shell-bridge-mixed-e2e",
        "capture_id": 123456,
    }
    canvas_width = 2521
    canvas_height = 761
    view_scale = min(480 / canvas_width, 480 / canvas_height, 1)
    return [
        {
            **common,
            "event": "capture_requested",
            "elapsed_us": 0,
            "fields": {
                "source": "gnome_shell_bridge",
                "origin": "request_receipt",
            },
        },
        {
            **common,
            "event": "shell_bridge_region_received",
            "elapsed_us": 1_000,
            "fields": {
                "bridge_capture_id": "1",
                "encoded_bytes": 76_543,
                "pixel_width": 5042,
                "pixel_height": 1522,
            },
        },
        {
            **common,
            "event": "capture_widget_show_requested",
            "elapsed_us": 10_000,
        },
        {
            **common,
            "event": "capture_widget_paint_completed",
            "elapsed_us": 15_000,
            "fields": {"width": canvas_width, "height": canvas_height},
        },
        {
            **common,
            "event": "region_editor_view_configured",
            "elapsed_us": 16_000,
            "fields": {
                "fit_mode": "whole_canvas",
                "canvas_width": canvas_width,
                "canvas_height": canvas_height,
                "output_bound_width": 480,
                "output_bound_height": 480,
                "window_width": 480,
                "window_height": 480,
                "viewport_width": 480,
                "viewport_height": 480,
                "view_scale_ppm": round(view_scale * 1_000_000),
            },
        },
        {
            **common,
            "event": "editor_ready",
            "elapsed_us": 20_000,
        },
        {
            **common,
            "event": "editor_committed",
            "elapsed_us": 30_000,
        },
        {
            **common,
            "event": "region_editor_view_changed",
            "elapsed_us": 31_000,
            "fields": {
                "mode": "one_to_one",
                "view_scale_ppm": 1_000_000,
                "viewport_width": 480,
                "viewport_height": 480,
            },
        },
        {
            **common,
            "event": "region_editor_view_panned",
            "elapsed_us": 32_000,
            "fields": {
                "view_scale_ppm": 1_000_000,
                "offset_delta_x": 80,
                "offset_delta_y": 60,
                "viewport_width": 480,
                "viewport_height": 480,
            },
        },
        {
            **common,
            "event": "region_editor_view_changed",
            "elapsed_us": 33_000,
            "fields": {
                "mode": "whole_canvas",
                "view_scale_ppm": round(view_scale * 1_000_000),
                "viewport_width": 480,
                "viewport_height": 480,
            },
        },
    ]


def event_named(events, name):
    return next(event for event in events if event["event"] == name)


def trace_named(traces, name):
    return next(trace for trace in traces if trace["event"] == name)


def bind_bridge_selection(events, result, traces, selection):
    selection = copy.deepcopy(selection)
    event_named(events, "selection-started").update(
        {"x": selection["x"], "y": selection["y"]}
    )
    for name in ("selection", "handoff-started"):
        event_named(events, name).update(selection)
    pixel_width = selection["width"] * 2
    pixel_height = selection["height"] * 2
    event_named(events, "handoff-encoded").update(
        {"pixel_width": pixel_width, "pixel_height": pixel_height}
    )
    received = trace_named(traces, "shell_bridge_region_received")["fields"]
    received.update({"pixel_width": pixel_width, "pixel_height": pixel_height})
    paint = trace_named(traces, "capture_widget_paint_completed")["fields"]
    paint.update({"width": selection["width"], "height": selection["height"]})
    view = trace_named(traces, "region_editor_view_configured")["fields"]
    view.update(
        {
            "canvas_width": selection["width"],
            "canvas_height": selection["height"],
            "view_scale_ppm": round(
                min(
                    view["viewport_width"] / selection["width"],
                    view["viewport_height"] / selection["height"],
                    1,
                )
                * 1_000_000
            ),
        }
    )
    result["selection"] = copy.deepcopy(selection)


def render(
    events,
    proof,
    result,
    traces=None,
    *,
    physical_proof=None,
    encoder_after_trigger=False,
    physical_after_logical=False,
    pass_first=False,
    navigation_after_pass=False,
    post_pass_event=None,
):
    traces = valid_traces() if traces is None else traces
    physical_proof = (
        copy.deepcopy(result["physical_encoder_proof"])
        if physical_proof is None
        else physical_proof
    )
    event_lines = [
        f"GNOME Shell-Message: [snipsnap-shell-bridge] {json.dumps(event)}"
        for event in events
    ]
    encoder_line = f"GNOME Shell-Message: MIXED-ENCODER-PASS {json.dumps(proof)}"
    physical_encoder_line = (
        "GNOME Shell-Message: PHYSICAL-ENCODER-PASS "
        f"{json.dumps(physical_proof)}"
    )
    trace_lines = [f"SNIPSNAP_CAPTURE_TRACE {json.dumps(trace)}" for trace in traces]
    pass_line = f"GNOME Shell-Message: MIXED-E2E-PASS {json.dumps(result)}"
    if encoder_after_trigger:
        body = [
            event_lines[0], physical_encoder_line, event_lines[1], encoder_line,
            *event_lines[2:],
        ]
    elif physical_after_logical:
        body = [event_lines[0], encoder_line, physical_encoder_line, *event_lines[1:]]
    else:
        body = [
            event_lines[0], physical_encoder_line, encoder_line, *event_lines[1:],
        ]
    if pass_first:
        lines = [pass_line, *body, *trace_lines]
    elif navigation_after_pass:
        navigation_markers = (
            '"event": "region_editor_view_changed"',
            '"event": "region_editor_view_panned"',
        )
        navigation_lines = [
            line for line in trace_lines if any(mark in line for mark in navigation_markers)
        ]
        core_trace_lines = [line for line in trace_lines if line not in navigation_lines]
        lines = [*body, *core_trace_lines, pass_line, *navigation_lines]
    else:
        lines = [*body, *trace_lines, pass_line]
    if post_pass_event is not None:
        lines.append(
            "GNOME Shell-Message: [snipsnap-shell-bridge] "
            + json.dumps(post_pass_event)
        )
    return "\n".join(lines)


class GnomeShellBridgeMixedEvidenceTest(unittest.TestCase):
    def test_accepts_exact_mixed_scale_encoder_and_editor_lifecycle(self):
        events, proof, result = valid_records()

        summary = evidence.validate_log(render(events, proof, result))

        self.assertEqual(summary["capture_id"], 1)
        self.assertEqual(summary["native_capture_id"], 123456)
        self.assertEqual(summary["monitors"], 4)
        self.assertEqual(summary["capture_scale"], 2)
        self.assertEqual(summary["texture_width"], 5120)
        self.assertEqual(summary["texture_height"], 1600)
        self.assertEqual(summary["selection_width"], 2521)
        self.assertEqual(summary["selection_height"], 761)
        self.assertEqual(summary["pixel_width"], 5042)
        self.assertEqual(summary["pixel_height"], 1522)
        self.assertEqual(summary["topology_id"], "v1-fd50135f")
        self.assertEqual(summary["encoder_topology_id"], "v1-788a8fa9")
        self.assertEqual(
            summary["physical_encoder_topology_id"], "v1-62d492f3"
        )
        self.assertTrue(summary["stage_hole_verified"])

    def test_accepts_alternate_compositor_snapped_selection(self):
        events, proof, result = valid_records()
        traces = valid_traces()
        bind_bridge_selection(
            events,
            result,
            traces,
            {"x": 0, "y": 100, "width": 2521, "height": 601},
        )

        summary = evidence.validate_log(render(events, proof, result, traces))

        self.assertEqual(summary["selection_width"], 2521)
        self.assertEqual(summary["pixel_width"], 5042)

    def test_rejects_missing_or_duplicate_physical_encoder_proof(self):
        events, proof, result = valid_records()
        log = render(events, proof, result)
        physical_line = next(
            line for line in log.splitlines() if "PHYSICAL-ENCODER-PASS " in line
        )

        with self.assertRaisesRegex(evidence.EvidenceError, "one physical encoder"):
            evidence.validate_log(
                "\n".join(line for line in log.splitlines() if line != physical_line)
            )
        with self.assertRaisesRegex(evidence.EvidenceError, "one physical encoder"):
            evidence.validate_log(log + "\n" + physical_line)

    def test_rejects_physical_encoder_after_logical_encoder(self):
        events, proof, result = valid_records()

        with self.assertRaisesRegex(evidence.EvidenceError, "physical.*precede.*logical"):
            evidence.validate_log(
                render(events, proof, result, physical_after_logical=True)
            )

    def test_rejects_physical_layout_mode_or_stage_view_scale_mutation(self):
        for field, value, message in (
            ("capture_layout_mode", "logical", "capture_layout_mode"),
            ("stage_view_scales", [1, 1, 1, 2], "stage_view_scales"),
        ):
            with self.subTest(field=field):
                events, proof, result = valid_records()
                result["physical_encoder_proof"][field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result))

    def test_rejects_physical_dimensions_content_or_topology_mutation(self):
        mutations = (
            ("texture_width", 3639, "stage-scale contract"),
            ("pixel_height", 1559, "scaled selection"),
            ("landmarks", 3, "four monitor landmarks"),
            ("stage_hole_verified", False, "staggered stage hole"),
            ("topology_id", "v1-deadbeef", "topology generation"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field):
                events, proof, result = valid_records()
                result["physical_encoder_proof"][field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result))

    def test_rejects_pass_physical_encoder_proof_mismatch(self):
        events, proof, result = valid_records()
        standalone = copy.deepcopy(result["physical_encoder_proof"])
        result["physical_encoder_proof"]["landmarks"] = 3

        with self.assertRaisesRegex(evidence.EvidenceError, "physical encoder proof differs"):
            evidence.validate_log(
                render(events, proof, result, physical_proof=standalone)
            )

    def test_rejects_mutated_encoder_monitor_scale(self):
        events, proof, result = valid_records()
        proof["monitor_layout"][1]["scale"] = 1
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "encoder proof.*layout"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_mutated_encoder_topology_hash(self):
        events, proof, result = valid_records()
        proof["topology_id"] = "v1-deadbeef"
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "topology generation"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_missing_monitor_landmark_proof(self):
        events, proof, result = valid_records()
        proof["landmarks"] = 3
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "four monitor landmarks"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_missing_stage_hole_proof(self):
        events, proof, result = valid_records()
        proof["stage_hole_verified"] = False
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "staggered stage hole"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_encoder_pixels_not_bound_to_scale_two_selection(self):
        events, proof, result = valid_records()
        proof["pixel_width"] -= 1
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "scaled selection"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_encoder_texture_not_bound_to_full_stage(self):
        events, proof, result = valid_records()
        proof["texture_height"] -= 1
        result["encoder_proof"] = copy.deepcopy(proof)

        with self.assertRaisesRegex(evidence.EvidenceError, "stage-scale contract"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_encoder_proof_after_print_trigger(self):
        events, proof, result = valid_records()

        with self.assertRaisesRegex(evidence.EvidenceError, "precede.*real trigger"):
            evidence.validate_log(
                render(events, proof, result, encoder_after_trigger=True)
            )

    def test_rejects_capture_ready_topology_mutation(self):
        events, proof, result = valid_records()
        event_named(events, "capture-ready")["monitor_layout"][3]["x"] = 2079

        with self.assertRaisesRegex(evidence.EvidenceError, "capture-ready layout"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_capture_ready_scale_or_texture_mutation(self):
        for field, value, message in (
            ("scale", 1.5, "capture-ready scale"),
            ("texture_width", 5119, "max-scale-2 stage"),
        ):
            with self.subTest(field=field):
                events, proof, result = valid_records()
                event_named(events, "capture-ready")[field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result))

    def test_rejects_capture_ready_view_scale_or_layout_mode_mutation(self):
        for field, value, message in (
            ("capture_scale", 1, "capture-ready capture_scale"),
            ("capture_layout_mode", "physical", "capture_layout_mode"),
            ("stage_view_scales", [1, 1, 1, 1], "stage_view_scales"),
        ):
            with self.subTest(field=field):
                events, proof, result = valid_records()
                event_named(events, "capture-ready")[field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result))

    def test_rejects_topology_generation_hash_mismatch(self):
        events, proof, result = valid_records()
        event_named(events, "capture-ready")["topology_generation"] = 6

        with self.assertRaisesRegex(evidence.EvidenceError, "topology generation"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_topology_identity_change_before_handoff(self):
        events, proof, result = valid_records()
        event_named(events, "handoff-started")["topology_id"] = "v1-deadbeef"

        with self.assertRaisesRegex(evidence.EvidenceError, "topology differs"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_selection_outside_stage(self):
        events, proof, result = valid_records()
        event_named(events, "selection").update({"x": 100, "width": 2500})
        event_named(events, "selection-started")["x"] = 100

        with self.assertRaisesRegex(evidence.EvidenceError, "bounded partial-stage"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_selection_that_does_not_span_all_four_monitors(self):
        events, proof, result = valid_records()
        event_named(events, "selection").update(
            {"x": 480, "y": 160, "width": 1600, "height": 480}
        )
        event_named(events, "selection-started").update({"x": 480, "y": 160})

        with self.assertRaisesRegex(evidence.EvidenceError, "all four monitor views"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_handoff_geometry_not_bound_to_selection(self):
        events, proof, result = valid_records()
        event_named(events, "handoff-started")["width"] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "differs from selection"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_encoded_pixels_not_bound_to_bridge_selection(self):
        events, proof, result = valid_records()
        event_named(events, "handoff-encoded")["pixel_width"] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "encoded pixel dimensions"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_mismatched_bridge_capture_id(self):
        events, proof, result = valid_records()
        event_named(events, "handoff-encoded")["capture_id"] = 2

        with self.assertRaisesRegex(evidence.EvidenceError, "mismatched capture_id"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_duplicate_or_out_of_order_lifecycle_event(self):
        events, proof, result = valid_records()
        events.insert(3, copy.deepcopy(event_named(events, "capture-ready")))
        with self.assertRaisesRegex(
            evidence.EvidenceError, "exactly one 'capture-ready'"
        ):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        events[9], events[10] = events[10], events[9]
        events[9]["monotonic_us"] = 1_791_000
        events[10]["monotonic_us"] = 1_792_000
        with self.assertRaisesRegex(evidence.EvidenceError, "out of order"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_decreasing_bridge_time(self):
        events, proof, result = valid_records()
        event_named(events, "handoff-started")["elapsed_us"] = 500_000

        with self.assertRaisesRegex(
            evidence.EvidenceError, "elapsed_us values decrease"
        ):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_forbidden_bridge_failure(self):
        events, proof, result = valid_records()
        events.insert(
            -1,
            {
                "component": "snipsnap-shell-bridge",
                "event": "handoff-failed",
                "capture_id": 1,
                "monotonic_us": 1_792_500,
            },
        )

        with self.assertRaisesRegex(evidence.EvidenceError, "forbidden bridge event"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_native_trace_run_or_capture_identity_mutation(self):
        events, proof, result = valid_records()
        traces = valid_traces()
        traces[2]["run_id"] = "wrong-run"
        with self.assertRaisesRegex(evidence.EvidenceError, "unexpected run_id"):
            evidence.validate_log(render(events, proof, result, traces))

        traces = valid_traces()
        traces[4]["capture_id"] += 1
        with self.assertRaisesRegex(evidence.EvidenceError, "capture_id changed"):
            evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_native_receiver_handoff_mutation(self):
        events, proof, result = valid_records()
        traces = valid_traces()
        trace_named(traces, "shell_bridge_region_received")["fields"][
            "pixel_height"
        ] -= 1

        with self.assertRaisesRegex(evidence.EvidenceError, "native receiver trace"):
            evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_native_editor_size_not_bound_to_logical_selection(self):
        for delta in (-1, 1):
            with self.subTest(paint_width_delta=delta):
                events, proof, result = valid_records()
                traces = valid_traces()
                trace_named(traces, "capture_widget_paint_completed")["fields"][
                    "width"
                ] = event_named(events, "selection")["width"] + delta

                with self.assertRaisesRegex(evidence.EvidenceError, "exactly equal"):
                    evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_native_editor_host_or_view_scale_mutation(self):
        for field, value, message in (
            ("output_bound_width", 481, "output bound"),
            ("window_width", 481, "exceeds"),
            ("view_scale_ppm", 123456, "view scale"),
        ):
            with self.subTest(field=field):
                events, proof, result = valid_records()
                traces = valid_traces()
                trace_named(traces, "region_editor_view_configured")["fields"][
                    field
                ] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_missing_or_false_editor_navigation(self):
        events, proof, result = valid_records()
        traces = [
            trace
            for trace in valid_traces()
            if trace["event"] != "region_editor_view_panned"
        ]
        with self.assertRaisesRegex(evidence.EvidenceError, "1:1, pan, and fit"):
            evidence.validate_log(render(events, proof, result, traces))

        events, proof, result = valid_records()
        traces = valid_traces()
        trace_named(traces, "region_editor_view_panned")["fields"].update(
            {"offset_delta_x": 0, "offset_delta_y": 0}
        )
        with self.assertRaisesRegex(evidence.EvidenceError, "did not move"):
            evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_navigation_after_pass(self):
        events, proof, result = valid_records()
        with self.assertRaisesRegex(evidence.EvidenceError, "precedes completed"):
            evidence.validate_log(
                render(
                    events,
                    proof,
                    result,
                    navigation_after_pass=True,
                )
            )

    def test_rejects_editor_landmark_frame_or_navigation_artifact_mutation(self):
        events, proof, result = valid_records()
        result["editor_landmarks_verified"] = False
        with self.assertRaisesRegex(evidence.EvidenceError, "captured landmarks"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["helper_window_removed"] = False
        with self.assertRaisesRegex(evidence.EvidenceError, "test chrome"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["editor_frame"]["height"] -= 1
        with self.assertRaisesRegex(evidence.EvidenceError, "editor_frame differs"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["editor_one_to_one_screenshot"] = (
            "/tmp/other/mixed-editor-one-to-one.png"
        )
        with self.assertRaisesRegex(evidence.EvidenceError, "one output directory"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_portal_or_monitor_picker_trace(self):
        for forbidden in ("portal_capture_started", "monitor_picker_shown"):
            with self.subTest(event=forbidden):
                events, proof, result = valid_records()
                traces = valid_traces()
                extra = copy.deepcopy(traces[2])
                extra["event"] = forbidden
                extra["elapsed_us"] = 12_000
                traces.insert(3, extra)
                with self.assertRaisesRegex(
                    evidence.EvidenceError, "portal|picker|forbidden native"
                ):
                    evidence.validate_log(render(events, proof, result, traces))

    def test_rejects_error_and_critical_log_markers(self):
        for marker in (
            "MIXED-E2E-FAIL assertion",
            "Gjs-ERROR **: exception",
            "libmutter-CRITICAL assertion",
        ):
            with self.subTest(marker=marker):
                events, proof, result = valid_records()
                with self.assertRaisesRegex(evidence.EvidenceError, "fatal"):
                    evidence.validate_log(render(events, proof, result) + "\n" + marker)

    def test_rejects_editor_pid_or_title_mutation(self):
        events, proof, result = valid_records()
        event_named(events, "commit-accepted")["daemon_pid"] += 1
        with self.assertRaisesRegex(evidence.EvidenceError, "commit daemon_pid"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        event_named(events, "editor-window-observed")["title"] = "SnipSnap"
        with self.assertRaisesRegex(evidence.EvidenceError, "title"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_pass_focus_mapping_identity_or_closure_mutation(self):
        mutations = (
            ("editor_focused", False, "editor_focused"),
            ("editor_mapped", False, "editor_mapped"),
            ("overlay_closed", False, "overlay_closed"),
            ("identities", ["not-snipsnap"], "accepted editor identity"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field):
                events, proof, result = valid_records()
                result[field] = value
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.validate_log(render(events, proof, result))

    def test_rejects_pass_encoder_or_topology_mutation(self):
        events, proof, result = valid_records()
        result["encoder_proof"]["landmarks"] = 3
        with self.assertRaisesRegex(evidence.EvidenceError, "encoder proof differs"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["monitor_layout"][2]["y"] = 159
        with self.assertRaisesRegex(evidence.EvidenceError, "MIXED-E2E-PASS layout"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_pass_selection_or_span_mutation(self):
        events, proof, result = valid_records()
        result["selection"]["width"] -= 1
        with self.assertRaisesRegex(evidence.EvidenceError, "selection differs"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["selection_spans_monitors"] = 3
        with self.assertRaisesRegex(evidence.EvidenceError, "all four monitors"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_pass_before_completion_or_lifecycle_after_pass(self):
        events, proof, result = valid_records()
        with self.assertRaisesRegex(evidence.EvidenceError, "precedes|appears after"):
            evidence.validate_log(render(events, proof, result, pass_first=True))

        post_pass = copy.deepcopy(event_named(events, "selection"))
        post_pass["monotonic_us"] = 2_000_000
        with self.assertRaisesRegex(evidence.EvidenceError, "appears after"):
            evidence.validate_log(
                render(events, proof, result, post_pass_event=post_pass)
            )

    def test_rejects_unbound_artifact_directory(self):
        events, proof, result = valid_records()
        result["editor_screenshot"] = "/tmp/other/mixed-editor.png"

        with self.assertRaisesRegex(evidence.EvidenceError, "one output directory"):
            evidence.validate_log(render(events, proof, result))

        events, proof, result = valid_records()
        result["physical_encoder_proof"]["png_path"] = (
            "/tmp/other/physical-selected.png"
        )
        with self.assertRaisesRegex(evidence.EvidenceError, "one output directory"):
            evidence.validate_log(render(events, proof, result))

    def test_rejects_malformed_structured_evidence(self):
        events, proof, result = valid_records()
        log = render(events, proof, result)
        log += "\nMIXED-ENCODER-PASS {not-json}"

        with self.assertRaisesRegex(evidence.EvidenceError, "malformed JSON"):
            evidence.validate_log(log)


if __name__ == "__main__":
    unittest.main()
