#!/usr/bin/env python3
"""Validate the GNOME Shell bridge compositor-to-editor evidence log."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any


BRIDGE_MARKER = "[snipsnap-shell-bridge] "
TRACE_MARKER = "SNIPSNAP_CAPTURE_TRACE "
PASS_MARKER = "E2E-PASS "
TRACE_SCHEMA = "snipsnap.capture-trace.v1"
TRACE_RUN_ID = "gnome-shell-bridge-e2e"
EXPECTED_MONITOR_LAYOUT = [
    {"index": 0, "x": 0, "y": 0, "width": 1280, "height": 720, "scale": 1},
    {"index": 1, "x": 1280, "y": 0, "width": 1280, "height": 720, "scale": 1},
    {"index": 2, "x": 2560, "y": 0, "width": 1280, "height": 720, "scale": 1},
]
EXPECTED_STAGE_WIDTH = 3840
EXPECTED_STAGE_HEIGHT = 720
EXPECTED_STAGE_VIEW_SCALES = [1, 1, 1]
CAPTURE_READY_BUDGET_US = 100_000
FIRST_PAINT_BUDGET_US = 100_000
HEADLESS_ENCODE_SANITY_BUDGET_US = 150_000
HEADLESS_POST_ENCODE_SANITY_BUDGET_US = 150_000
HEADLESS_HANDOFF_SANITY_BUDGET_US = 250_000
MAX_ELAPSED_CLOCK_SKEW_US = 10_000
REQUIRED_EVENTS = (
    "enabled",
    "trigger",
    "capture-ready",
    "first-paint",
    "selection",
    "handoff-started",
    "handoff-encoded",
    "editor-window-observed",
    "commit-accepted",
    "overlay-closed",
    "editor-focus-requested",
)
REQUIRED_TRACE_EVENTS = (
    "capture_requested",
    "shell_bridge_region_received",
    "capture_widget_show_requested",
    "capture_widget_paint_completed",
    "region_editor_view_configured",
    "editor_ready",
    "editor_committed",
)
FORBIDDEN_EVENTS = {
    "keybinding-unavailable",
    "trigger-refused",
    "capture-discarded",
    "capture-failed",
    "cancel-refused",
    "cancelled",
    "handoff-refused",
    "handoff-failed",
    "commit-window-lost",
    "commit-ack-indeterminate",
}
FORBIDDEN_TRACE_EVENTS = {
    "monitor_picker_shown",
    "portal_capture_started",
    "portal_capture_failed",
}
FATAL_LOG_MARKERS = (
    "E2E-FAIL",
    "JS ERROR",
    "CRITICAL",
)
CAPTURE_EVENTS = {
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
} | FORBIDDEN_EVENTS
TIMED_EVENTS = (
    "capture-ready",
    "first-paint",
    "selection",
    "handoff-started",
    "handoff-encoded",
    "commit-accepted",
)

Record = tuple[int, dict[str, Any]]


class EvidenceError(ValueError):
    """Raised when an E2E log does not prove the required lifecycle."""


def _records_after_marker(text: str, marker: str) -> list[Record]:
    records: list[Record] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        marker_index = line.find(marker)
        if marker_index < 0:
            continue
        payload = line[marker_index + len(marker) :].strip()
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise EvidenceError(
                f"line {line_number} has malformed JSON after {marker.strip()!r}"
            ) from error
        if not isinstance(value, dict):
            raise EvidenceError(
                f"line {line_number} JSON after {marker.strip()!r} is not an object"
            )
        records.append((line_number, value))
    return records


def _require_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if minimum == 0 else f">= {minimum}"
        raise EvidenceError(f"{label} must be an integer {qualifier}")
    return value


def _require_positive_int(value: Any, label: str) -> int:
    return _require_int(value, label, minimum=1)


def _require_signed_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(f"{label} must be an integer")
    return value


def _require_editor_view(
    paint_fields: Any,
    view_fields: Any,
    selection: dict[str, int],
) -> int:
    if not isinstance(paint_fields, dict) or not isinstance(view_fields, dict):
        raise EvidenceError("native editor view traces must contain fields objects")
    canvas = (
        _require_positive_int(view_fields.get("canvas_width"), "canvas_width"),
        _require_positive_int(view_fields.get("canvas_height"), "canvas_height"),
    )
    expected_canvas = (selection["width"], selection["height"])
    paint = (
        _require_positive_int(paint_fields.get("width"), "native editor width"),
        _require_positive_int(paint_fields.get("height"), "native editor height"),
    )
    if canvas != expected_canvas or paint != expected_canvas:
        raise EvidenceError(
            "native editor canvas must exactly equal the logical selection"
        )
    if view_fields.get("fit_mode") != "whole_canvas":
        raise EvidenceError("native editor must begin in whole_canvas fit mode")

    output_bound = (
        _require_positive_int(
            view_fields.get("output_bound_width"), "output_bound_width"
        ),
        _require_positive_int(
            view_fields.get("output_bound_height"), "output_bound_height"
        ),
    )
    if output_bound != (1280, 720):
        raise EvidenceError(
            "native editor output bound differs from one headless output"
        )
    window = (
        _require_positive_int(view_fields.get("window_width"), "window_width"),
        _require_positive_int(view_fields.get("window_height"), "window_height"),
    )
    viewport = (
        _require_positive_int(view_fields.get("viewport_width"), "viewport_width"),
        _require_positive_int(view_fields.get("viewport_height"), "viewport_height"),
    )
    if (
        window[0] > output_bound[0]
        or window[1] > output_bound[1]
        or viewport[0] > window[0]
        or viewport[1] > window[1]
    ):
        raise EvidenceError("native editor host exceeds its compositor output bound")

    expected_scale_ppm = round(
        min(viewport[0] / canvas[0], viewport[1] / canvas[1], 1) * 1_000_000
    )
    scale_ppm = _require_positive_int(
        view_fields.get("view_scale_ppm"), "view_scale_ppm"
    )
    if scale_ppm > 1_000_000 or abs(scale_ppm - expected_scale_ppm) > 1:
        raise EvidenceError("native editor view scale does not fit the whole canvas")
    return scale_ppm


def _require_editor_navigation(
    trace_records: list[Record],
    committed_line: int,
    fit_scale_ppm: int,
    viewport: tuple[int, int],
) -> int:
    changes = [
        record
        for record in trace_records
        if record[1].get("event") == "region_editor_view_changed"
    ]
    pans = [
        record
        for record in trace_records
        if record[1].get("event") == "region_editor_view_panned"
    ]
    if len(changes) != 2 or len(pans) != 1:
        raise EvidenceError("native editor must prove 1:1, pan, and fit navigation")
    if not (committed_line < changes[0][0] < pans[0][0] < changes[1][0]):
        raise EvidenceError("native editor navigation trace is out of order")

    expected = (
        (changes[0], "one_to_one", 1_000_000),
        (pans[0], None, 1_000_000),
        (changes[1], "whole_canvas", fit_scale_ppm),
    )
    for record, mode, scale_ppm in expected:
        fields = record[1].get("fields")
        if not isinstance(fields, dict):
            raise EvidenceError("native editor navigation trace has no fields")
        if mode is not None and fields.get("mode") != mode:
            raise EvidenceError("native editor navigation mode is incorrect")
        if fields.get("view_scale_ppm") != scale_ppm:
            raise EvidenceError("native editor navigation scale is incorrect")
        if (
            fields.get("viewport_width"),
            fields.get("viewport_height"),
        ) != viewport:
            raise EvidenceError("native editor navigation viewport changed")
    pan_fields = pans[0][1]["fields"]
    pan_delta = (
        _require_signed_int(pan_fields.get("offset_delta_x"), "pan offset_delta_x"),
        _require_signed_int(pan_fields.get("offset_delta_y"), "pan offset_delta_y"),
    )
    if pan_delta == (0, 0):
        raise EvidenceError("native editor pan did not move the canvas")
    return changes[-1][0]


def _artifact_path(value: Any, basename: str, label: str) -> pathlib.PurePath:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} must be a non-empty path")
    path = pathlib.PurePath(value)
    if not path.is_absolute() or path.name != basename:
        raise EvidenceError(f"{label} must be an absolute {basename} path")
    return path


def _require_window_frame(value: Any, window: tuple[int, int]) -> dict[str, int]:
    if not isinstance(value, dict):
        raise EvidenceError("editor_frame must be an object")
    frame = {
        "x": _require_int(value.get("x"), "editor_frame.x"),
        "y": _require_int(value.get("y"), "editor_frame.y"),
        "width": _require_positive_int(value.get("width"), "editor_frame.width"),
        "height": _require_positive_int(
            value.get("height"), "editor_frame.height"
        ),
    }
    if (frame["width"], frame["height"]) != window:
        raise EvidenceError("editor_frame differs from the native editor window trace")
    primary = EXPECTED_MONITOR_LAYOUT[0]
    if (
        frame["x"] != primary["x"]
        or frame["width"] != primary["width"]
        or frame["y"] <= primary["y"]
        or frame["height"] >= primary["height"]
        or frame["y"] + frame["height"] != primary["y"] + primary["height"]
    ):
        raise EvidenceError(
            "editor_frame must prove the primary output's panel-constrained work area"
        )
    if (
        frame["x"] + frame["width"] > EXPECTED_STAGE_WIDTH
        or frame["y"] + frame["height"] > EXPECTED_STAGE_HEIGHT
    ):
        raise EvidenceError("editor_frame is outside the compositor stage")
    return frame


def _one_record(records: list[Record], name: str) -> Record:
    matches = [record for record in records if record[1].get("event") == name]
    if len(matches) != 1:
        raise EvidenceError(
            f"expected exactly one {name!r} event, found {len(matches)}"
        )
    return matches[0]


def _require_scale_one(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != 1:
        raise EvidenceError(f"{label} must equal scale 1")
    return 1


def _require_topology_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"v1-[0-9a-f]{8}", value) is None:
        raise EvidenceError(f"{label} must be a v1 topology ID")
    return value


def _calculated_topology_id(generation: int) -> str:
    signature = json.dumps(
        {
            "version": 1,
            "generation": generation,
            "stage_width": EXPECTED_STAGE_WIDTH,
            "stage_height": EXPECTED_STAGE_HEIGHT,
            "monitors": EXPECTED_MONITOR_LAYOUT,
            "stage_view_scales": EXPECTED_STAGE_VIEW_SCALES,
        },
        separators=(",", ":"),
    )
    value = 0x811C9DC5
    for character in signature:
        value ^= ord(character)
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"v1-{value:08x}"


def _require_exact_monitor_layout(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list) or len(value) != 3:
        raise EvidenceError("E2E-PASS monitor_layout must contain three rectangles")
    layout: list[dict[str, int]] = []
    for index, rectangle in enumerate(value):
        if not isinstance(rectangle, dict):
            raise EvidenceError(f"monitor_layout[{index}] must be an object")
        normalized = {
            "index": _require_int(
                rectangle.get("index"), f"monitor_layout[{index}].index"
            ),
            "x": _require_int(rectangle.get("x"), f"monitor_layout[{index}].x"),
            "y": _require_int(rectangle.get("y"), f"monitor_layout[{index}].y"),
            "width": _require_positive_int(
                rectangle.get("width"), f"monitor_layout[{index}].width"
            ),
            "height": _require_positive_int(
                rectangle.get("height"), f"monitor_layout[{index}].height"
            ),
            "scale": _require_scale_one(
                rectangle.get("scale"), f"monitor_layout[{index}].scale"
            ),
        }
        layout.append(normalized)
    if layout != EXPECTED_MONITOR_LAYOUT:
        raise EvidenceError(
            "monitor topology is not the expected non-overlapping 3x1280x720 layout"
        )
    return layout


def _selection_geometry(event: dict[str, Any], label: str) -> dict[str, int]:
    return {
        "x": _require_int(event.get("x"), f"{label} x"),
        "y": _require_int(event.get("y"), f"{label} y"),
        "width": _require_positive_int(event.get("width"), f"{label} width"),
        "height": _require_positive_int(event.get("height"), f"{label} height"),
    }


def validate_log(text: str) -> dict[str, Any]:
    """Validate one isolated three-monitor bridge capture and return a summary."""
    for marker in FATAL_LOG_MARKERS:
        if marker in text:
            raise EvidenceError(f"fatal compositor/test marker present: {marker}")

    event_records = _records_after_marker(text, BRIDGE_MARKER)
    if not event_records:
        raise EvidenceError("no structured bridge events found")
    events = [record[1] for record in event_records]
    if any(event.get("component") != "snipsnap-shell-bridge" for event in events):
        raise EvidenceError("bridge event has an unexpected component")

    previous_monotonic_us = 0
    for event in events:
        monotonic_us = _require_positive_int(
            event.get("monotonic_us"),
            f"{event.get('event')!r} monotonic_us",
        )
        if monotonic_us < previous_monotonic_us:
            raise EvidenceError("bridge monotonic_us values decrease")
        previous_monotonic_us = monotonic_us

    forbidden = [
        str(event.get("event"))
        for event in events
        if event.get("event") in FORBIDDEN_EVENTS
    ]
    if forbidden:
        raise EvidenceError(f"forbidden bridge event present: {forbidden[0]}")

    required_records = [_one_record(event_records, name) for name in REQUIRED_EVENTS]
    required_lines = [record[0] for record in required_records]
    if required_lines != sorted(required_lines):
        observed = [str(event.get("event")) for event in events]
        raise EvidenceError(f"required bridge events are out of order: {observed}")
    required = [record[1] for record in required_records]

    trigger = required[1]
    capture_id = _require_positive_int(trigger.get("capture_id"), "capture_id")
    for event in events:
        name = event.get("event")
        if name not in CAPTURE_EVENTS:
            continue
        event_capture_id = _require_positive_int(
            event.get("capture_id"), f"event {name!r} capture_id"
        )
        if event_capture_id != capture_id:
            raise EvidenceError(f"event {name!r} has a mismatched capture_id")

    timed = {
        event["event"]: event for event in required if event["event"] in TIMED_EVENTS
    }
    reported_elapsed: dict[str, int] = {}
    monotonic_elapsed: dict[str, int] = {}
    trigger_monotonic_us = trigger["monotonic_us"]
    for name in TIMED_EVENTS:
        reported_elapsed_us = _require_positive_int(
            timed[name].get("elapsed_us"), f"{name} elapsed_us"
        )
        monotonic_elapsed_us = timed[name]["monotonic_us"] - trigger_monotonic_us
        if monotonic_elapsed_us < 1:
            raise EvidenceError(f"{name} monotonic_us must follow trigger")
        if (
            abs(reported_elapsed_us - monotonic_elapsed_us)
            > MAX_ELAPSED_CLOCK_SKEW_US
        ):
            raise EvidenceError(
                f"{name} elapsed_us differs from the journal monotonic clock"
            )
        reported_elapsed[name] = reported_elapsed_us
        monotonic_elapsed[name] = monotonic_elapsed_us
    reported_elapsed_values = [reported_elapsed[name] for name in TIMED_EVENTS]
    if reported_elapsed_values != sorted(reported_elapsed_values):
        raise EvidenceError("bridge elapsed_us values decrease across the lifecycle")
    capture_ready_us = max(
        reported_elapsed["capture-ready"], monotonic_elapsed["capture-ready"]
    )
    first_paint_us = max(
        reported_elapsed["first-paint"], monotonic_elapsed["first-paint"]
    )
    reported_handoff_encode_us = (
        reported_elapsed["handoff-encoded"]
        - reported_elapsed["handoff-started"]
    )
    monotonic_handoff_encode_us = (
        timed["handoff-encoded"]["monotonic_us"]
        - timed["handoff-started"]["monotonic_us"]
    )
    reported_handoff_post_encode_us = (
        reported_elapsed["commit-accepted"]
        - reported_elapsed["handoff-encoded"]
    )
    monotonic_handoff_post_encode_us = (
        timed["commit-accepted"]["monotonic_us"]
        - timed["handoff-encoded"]["monotonic_us"]
    )
    for label, value in (
        ("reported selected-region encode", reported_handoff_encode_us),
        ("journal selected-region encode", monotonic_handoff_encode_us),
        ("reported selected-region post-encode", reported_handoff_post_encode_us),
        ("journal selected-region post-encode", monotonic_handoff_post_encode_us),
    ):
        if value < 1:
            raise EvidenceError(f"{label} duration must be positive")
    handoff_encode_us = max(
        reported_handoff_encode_us, monotonic_handoff_encode_us
    )
    handoff_post_encode_us = max(
        reported_handoff_post_encode_us, monotonic_handoff_post_encode_us
    )
    reported_handoff_us = (
        reported_elapsed["commit-accepted"]
        - reported_elapsed["handoff-started"]
    )
    monotonic_handoff_us = (
        timed["commit-accepted"]["monotonic_us"]
        - timed["handoff-started"]["monotonic_us"]
    )
    handoff_us = max(reported_handoff_us, monotonic_handoff_us)
    if capture_ready_us > CAPTURE_READY_BUDGET_US:
        raise EvidenceError("capture-ready exceeded the 100 ms headless budget")
    if first_paint_us > FIRST_PAINT_BUDGET_US:
        raise EvidenceError("first-paint exceeded the 100 ms headless budget")
    if handoff_encode_us > HEADLESS_ENCODE_SANITY_BUDGET_US:
        raise EvidenceError(
            "selected-region encode exceeded the 150 ms headless sanity budget"
        )
    if handoff_post_encode_us > HEADLESS_POST_ENCODE_SANITY_BUDGET_US:
        raise EvidenceError(
            "selected-region post-encode handoff exceeded the 150 ms "
            "headless sanity budget"
        )
    if handoff_us > HEADLESS_HANDOFF_SANITY_BUDGET_US:
        raise EvidenceError(
            "selected-region handoff exceeded the 250 ms headless sanity budget"
        )

    capture_ready = required[2]
    if (
        _require_positive_int(capture_ready.get("monitors"), "capture-ready monitors")
        != 3
    ):
        raise EvidenceError("capture-ready must report exactly three monitors")
    stage_width = _require_positive_int(
        capture_ready.get("stage_width"), "capture-ready stage_width"
    )
    stage_height = _require_positive_int(
        capture_ready.get("stage_height"), "capture-ready stage_height"
    )
    if (stage_width, stage_height) != (EXPECTED_STAGE_WIDTH, EXPECTED_STAGE_HEIGHT):
        raise EvidenceError("capture-ready stage is not exactly 3840x720")
    if _require_scale_one(capture_ready.get("scale"), "capture-ready scale") != 1:
        raise EvidenceError("capture-ready scale must be 1")
    if (
        _require_scale_one(
            capture_ready.get("capture_scale"), "capture-ready capture_scale"
        )
        != 1
    ):
        raise EvidenceError("capture-ready capture_scale must be 1")
    stage_view_scales = capture_ready.get("stage_view_scales")
    if (
        not isinstance(stage_view_scales, list)
        or len(stage_view_scales) != len(EXPECTED_STAGE_VIEW_SCALES)
        or any(
            _require_scale_one(scale, f"stage_view_scales[{index}]") != 1
            for index, scale in enumerate(stage_view_scales)
        )
    ):
        raise EvidenceError("capture-ready must bind three scale-1 StageViews")
    if capture_ready.get("capture_layout_mode") != "scale-one":
        raise EvidenceError("capture-ready capture_layout_mode must equal scale-one")
    if (
        _require_positive_int(
            capture_ready.get("texture_width"), "capture-ready texture_width"
        )
        != stage_width
        or _require_positive_int(
            capture_ready.get("texture_height"), "capture-ready texture_height"
        )
        != stage_height
    ):
        raise EvidenceError("capture-ready texture differs from the scale-1 stage")
    topology_id = _require_topology_id(
        capture_ready.get("topology_id"), "capture-ready topology_id"
    )
    topology_generation = _require_positive_int(
        capture_ready.get("topology_generation"),
        "capture-ready topology_generation",
    )
    if topology_id != _calculated_topology_id(topology_generation):
        raise EvidenceError(
            "capture-ready topology_id does not bind its exact topology generation"
        )
    _require_exact_monitor_layout(capture_ready.get("monitor_layout"))
    first_paint = required[3]
    if (
        _require_positive_int(first_paint.get("stage_width"), "first-paint stage_width")
        != stage_width
        or _require_positive_int(
            first_paint.get("stage_height"), "first-paint stage_height"
        )
        != stage_height
    ):
        raise EvidenceError("first-paint stage dimensions changed after capture")
    if (
        _require_topology_id(first_paint.get("topology_id"), "first-paint topology_id")
        != topology_id
    ):
        raise EvidenceError("first-paint topology differs from capture-ready")

    selection = required[4]
    selection_geometry = _selection_geometry(selection, "selection")
    if _require_positive_int(selection.get("scale"), "selection scale") != 1:
        raise EvidenceError("the fixed headless selection must use scale 1")
    if (
        _require_topology_id(selection.get("topology_id"), "selection topology_id")
        != topology_id
    ):
        raise EvidenceError("selection topology differs from capture-ready")
    if (
        selection_geometry["x"] + selection_geometry["width"] > stage_width
        or selection_geometry["y"] + selection_geometry["height"] > stage_height
        or selection_geometry["width"] >= stage_width
        or selection_geometry["height"] >= stage_height
    ):
        raise EvidenceError(
            "selection must remain in the stage and be smaller than the full stage"
        )
    if not (
        selection_geometry["x"] < 1280
        and selection_geometry["x"] + selection_geometry["width"] > 2560
    ):
        raise EvidenceError("selection geometry does not cross both monitor seams")

    handoff_started = required[5]
    if _selection_geometry(handoff_started, "handoff-started") != selection_geometry:
        raise EvidenceError("handoff-started geometry differs from the selection")
    if (
        _require_positive_int(handoff_started.get("scale"), "handoff-started scale")
        != 1
    ):
        raise EvidenceError("handoff-started scale differs from the scale-1 selection")
    if (
        _require_topology_id(
            handoff_started.get("topology_id"), "handoff-started topology_id"
        )
        != topology_id
    ):
        raise EvidenceError("handoff-started topology differs from capture-ready")

    encoded = required[6]
    pixel_width = _require_positive_int(
        encoded.get("pixel_width"), "handoff pixel_width"
    )
    pixel_height = _require_positive_int(
        encoded.get("pixel_height"), "handoff pixel_height"
    )
    png_bytes = _require_positive_int(
        encoded.get("png_bytes"), "handoff PNG byte count"
    )
    if (
        _require_topology_id(encoded.get("topology_id"), "handoff-encoded topology_id")
        != topology_id
    ):
        raise EvidenceError("handoff-encoded topology differs from capture-ready")
    if (pixel_width, pixel_height) != (
        selection_geometry["width"],
        selection_geometry["height"],
    ):
        raise EvidenceError(
            "encoded pixel dimensions differ from the scale-1 selection"
        )

    observed_window = required[7]
    daemon_pid = _require_positive_int(
        observed_window.get("daemon_pid"), "observed editor daemon_pid"
    )
    expected_title = f"SnipSnap [capture-id={capture_id}]"
    if observed_window.get("title") != expected_title:
        raise EvidenceError("observed editor title does not bind the capture ID")

    committed = required[8]
    if (
        _require_positive_int(committed.get("daemon_pid"), "commit daemon_pid")
        != daemon_pid
    ):
        raise EvidenceError("commit daemon_pid differs from the observed editor")
    overlay_closed = required[9]
    if overlay_closed.get("reason") != "commit-accepted":
        raise EvidenceError("overlay did not close because the commit was accepted")
    focus_requested = required[10]
    if (
        _require_positive_int(
            focus_requested.get("daemon_pid"), "focus request daemon_pid"
        )
        != daemon_pid
    ):
        raise EvidenceError("focus request daemon_pid differs from the observed editor")

    trace_records = _records_after_marker(text, TRACE_MARKER)
    if not trace_records:
        raise EvidenceError("no native SnipSnap capture trace found")
    traces = [record[1] for record in trace_records]
    if any(trace.get("schema") != TRACE_SCHEMA for trace in traces):
        raise EvidenceError("native capture trace has an unexpected schema")
    if any(trace.get("run_id") != TRACE_RUN_ID for trace in traces):
        raise EvidenceError("native capture trace has an unexpected run_id")
    forbidden_trace = [
        str(trace.get("event"))
        for trace in traces
        if trace.get("event") in FORBIDDEN_TRACE_EVENTS
    ]
    if forbidden_trace:
        raise EvidenceError(
            f"forbidden native trace event present: {forbidden_trace[0]}"
        )
    required_trace_records = [
        _one_record(trace_records, name) for name in REQUIRED_TRACE_EVENTS
    ]
    if [record[0] for record in required_trace_records] != sorted(
        record[0] for record in required_trace_records
    ):
        raise EvidenceError("native capture trace events are out of order")
    native_capture_id = _require_positive_int(
        required_trace_records[0][1].get("capture_id"), "native trace capture_id"
    )
    previous_trace_elapsed = -1
    for trace in traces:
        if (
            _require_positive_int(trace.get("capture_id"), "native trace capture_id")
            != native_capture_id
        ):
            raise EvidenceError("native trace capture_id changed during the handoff")
        elapsed_us = _require_int(trace.get("elapsed_us"), "native trace elapsed_us")
        if elapsed_us < previous_trace_elapsed:
            raise EvidenceError("native trace elapsed_us values decrease")
        previous_trace_elapsed = elapsed_us
    requested_fields = required_trace_records[0][1].get("fields")
    if (
        not isinstance(requested_fields, dict)
        or requested_fields.get("source") != "gnome_shell_bridge"
    ):
        raise EvidenceError(
            "native trace does not identify the GNOME Shell bridge source"
        )
    received_fields = required_trace_records[1][1].get("fields")
    if not isinstance(received_fields, dict):
        raise EvidenceError("native region-received trace has no fields object")
    if received_fields.get("bridge_capture_id") != str(capture_id):
        raise EvidenceError("native receiver trace does not bind the bridge capture ID")
    if (
        _require_positive_int(received_fields.get("pixel_width"), "native pixel_width")
        != pixel_width
        or _require_positive_int(
            received_fields.get("pixel_height"), "native pixel_height"
        )
        != pixel_height
        or _require_positive_int(
            received_fields.get("encoded_bytes"), "native encoded_bytes"
        )
        != png_bytes
    ):
        raise EvidenceError("native receiver trace differs from the encoded handoff")
    view_fields = required_trace_records[4][1].get("fields")
    fit_scale_ppm = _require_editor_view(
        required_trace_records[3][1].get("fields"),
        view_fields,
        selection_geometry,
    )
    if not isinstance(view_fields, dict):
        raise EvidenceError("native editor view trace has no fields")
    navigation_last_line = _require_editor_navigation(
        trace_records,
        required_trace_records[-1][0],
        fit_scale_ppm,
        (view_fields["viewport_width"], view_fields["viewport_height"]),
    )

    pass_records = _records_after_marker(text, PASS_MARKER)
    if len(pass_records) != 1:
        raise EvidenceError(
            f"expected exactly one E2E-PASS object, found {len(pass_records)}"
        )
    pass_line, result = pass_records[0]
    if pass_line <= required_records[-1][0]:
        raise EvidenceError("E2E-PASS precedes the completed bridge lifecycle")
    if pass_line <= required_trace_records[-1][0]:
        raise EvidenceError("E2E-PASS precedes the native commit trace")
    if pass_line <= navigation_last_line:
        raise EvidenceError("E2E-PASS precedes completed editor navigation")
    if any(
        line_number > pass_line and event.get("event") in CAPTURE_EVENTS
        for line_number, event in event_records
    ):
        raise EvidenceError("capture lifecycle event appears after E2E-PASS")
    if result.get("event") != "passed":
        raise EvidenceError("E2E-PASS object does not report event=passed")
    if (
        _require_positive_int(result.get("capture_id"), "E2E-PASS capture_id")
        != capture_id
    ):
        raise EvidenceError("E2E-PASS capture_id differs from the bridge lifecycle")
    if (
        _require_positive_int(result.get("daemon_pid"), "E2E-PASS daemon_pid")
        != daemon_pid
    ):
        raise EvidenceError("E2E-PASS daemon_pid differs from the observed editor")
    if _require_positive_int(result.get("monitors"), "E2E-PASS monitors") != 3:
        raise EvidenceError("E2E-PASS must report exactly three monitors")
    if (
        _require_positive_int(result.get("stage_width"), "E2E-PASS stage_width")
        != stage_width
        or _require_positive_int(result.get("stage_height"), "E2E-PASS stage_height")
        != stage_height
    ):
        raise EvidenceError("E2E-PASS stage dimensions differ from capture-ready")
    _require_exact_monitor_layout(result.get("monitor_layout"))
    if result.get("selection") != selection_geometry:
        raise EvidenceError("E2E-PASS selection differs from the bridge selection")
    if (
        _require_positive_int(
            result.get("selection_spans_monitors"), "selection_spans_monitors"
        )
        != 3
    ):
        raise EvidenceError("selection did not intersect all three monitors")
    for field in (
        "editor_normal",
        "editor_focused",
        "editor_showing",
        "editor_mapped",
        "overlay_closed",
    ):
        if result.get(field) is not True:
            raise EvidenceError(f"E2E-PASS must report {field}=true")
    if result.get("editor_title") != expected_title:
        raise EvidenceError("E2E-PASS editor title does not bind the capture ID")
    identities = result.get("identities")
    if not isinstance(identities, list) or not all(
        isinstance(identity, str) for identity in identities
    ):
        raise EvidenceError("E2E-PASS identities must be a string array")
    if not any(
        identity in {"snipsnap", "tech.norvi.snipsnap"} for identity in identities
    ):
        raise EvidenceError("E2E-PASS does not contain an accepted editor identity")
    window = (view_fields["window_width"], view_fields["window_height"])
    _require_window_frame(result.get("editor_frame"), window)
    overlay_path = _artifact_path(
        result.get("overlay_screenshot"), "overlay.png", "overlay_screenshot"
    )
    editor_path = _artifact_path(
        result.get("editor_screenshot"), "editor.png", "editor_screenshot"
    )
    one_to_one_path = _artifact_path(
        result.get("editor_one_to_one_screenshot"),
        "editor-one-to-one.png",
        "editor_one_to_one_screenshot",
    )
    if not overlay_path.parent == editor_path.parent == one_to_one_path.parent:
        raise EvidenceError("baseline E2E artifacts do not share one output directory")

    return {
        "capture_id": capture_id,
        "daemon_pid": daemon_pid,
        "monitors": 3,
        "stage_width": stage_width,
        "stage_height": stage_height,
        "topology_id": topology_id,
        "topology_generation": topology_generation,
        "capture_ready_us": capture_ready_us,
        "first_paint_us": first_paint_us,
        "handoff_encode_us": handoff_encode_us,
        "handoff_post_encode_us": handoff_post_encode_us,
        "handoff_us": handoff_us,
        "commit_accepted_us": max(
            reported_elapsed["commit-accepted"],
            monotonic_elapsed["commit-accepted"],
        ),
        "selection_width": selection_geometry["width"],
        "selection_height": selection_geometry["height"],
        "png_bytes": png_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=pathlib.Path, help="combined GNOME test log")
    args = parser.parse_args()

    try:
        summary = validate_log(args.log.read_text(encoding="utf-8"))
    except (EvidenceError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
