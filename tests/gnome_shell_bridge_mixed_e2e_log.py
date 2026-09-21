#!/usr/bin/env python3
"""Validate mixed-scale GNOME Shell bridge compositor evidence."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
from typing import Any


BRIDGE_MARKER = "[snipsnap-shell-bridge] "
TRACE_MARKER = "SNIPSNAP_CAPTURE_TRACE "
PHYSICAL_ENCODER_MARKER = "PHYSICAL-ENCODER-PASS "
ENCODER_MARKER = "MIXED-ENCODER-PASS "
PASS_MARKER = "MIXED-E2E-PASS "
TRACE_SCHEMA = "snipsnap.capture-trace.v1"
TRACE_RUN_ID = "gnome-shell-bridge-mixed-e2e"
EXPECTED_CAPTURE_ID = 1
EXPECTED_STAGE_WIDTH = 2560
EXPECTED_STAGE_HEIGHT = 800
EXPECTED_CAPTURE_SCALE = 2
EXPECTED_TEXTURE_WIDTH = 5120
EXPECTED_TEXTURE_HEIGHT = 1600
EXPECTED_STAGE_VIEW_SCALES = [1, 1.25, 1.5, 2]
EXPECTED_MONITOR_LAYOUT = [
    {"index": 0, "x": 0, "y": 0, "width": 480, "height": 800, "scale": 1},
    {
        "index": 1,
        "x": 480,
        "y": 160,
        "width": 800,
        "height": 480,
        "scale": 1.25,
    },
    {
        "index": 2,
        "x": 1280,
        "y": 160,
        "width": 800,
        "height": 480,
        "scale": 1.5,
    },
    {
        "index": 3,
        "x": 2080,
        "y": 0,
        "width": 480,
        "height": 800,
        "scale": 2,
    },
]
EXPECTED_ENCODER_SELECTION = {
    "x": 20,
    "y": 20,
    "width": 2520,
    "height": 760,
}
EXPECTED_ENCODER_PIXEL_WIDTH = 5040
EXPECTED_ENCODER_PIXEL_HEIGHT = 1520
EXPECTED_PHYSICAL_STAGE_WIDTH = 3640
EXPECTED_PHYSICAL_STAGE_HEIGHT = 1600
EXPECTED_PHYSICAL_CAPTURE_SCALE = 1
EXPECTED_PHYSICAL_TEXTURE_WIDTH = 3640
EXPECTED_PHYSICAL_TEXTURE_HEIGHT = 1600
EXPECTED_PHYSICAL_STAGE_VIEW_SCALES = [1, 1, 1, 1]
EXPECTED_PHYSICAL_MONITOR_LAYOUT = [
    {"index": 0, "x": 0, "y": 0, "width": 480, "height": 800, "scale": 1},
    {
        "index": 1,
        "x": 480,
        "y": 100,
        "width": 1000,
        "height": 600,
        "scale": 1,
    },
    {
        "index": 2,
        "x": 1480,
        "y": 40,
        "width": 1200,
        "height": 720,
        "scale": 1,
    },
    {
        "index": 3,
        "x": 2680,
        "y": 0,
        "width": 960,
        "height": 1600,
        "scale": 2,
    },
]
EXPECTED_PHYSICAL_ENCODER_SELECTION = {
    "x": 20,
    "y": 20,
    "width": 3600,
    "height": 1560,
}
EXPECTED_PHYSICAL_ENCODER_PIXEL_WIDTH = 3600
EXPECTED_PHYSICAL_ENCODER_PIXEL_HEIGHT = 1560
EXPECTED_LANDMARKS = 4
REQUIRED_EVENTS = (
    "enabled",
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
FORBIDDEN_EVENT_TERMS = (
    "failed",
    "refused",
    "cancelled",
    "discarded",
    "indeterminate",
)
FATAL_LOG_PATTERN = re.compile(
    r"MIXED-E2E-FAIL|(?:^|\s)E2E-FAIL|JS ERROR|\bCRITICAL\b|"
    r"(?:Gjs|gnome-shell|mutter|libmutter)-ERROR",
    re.IGNORECASE | re.MULTILINE,
)
RAW_PICKER_PATTERN = re.compile(
    r"monitor[_ -]picker|select (?:a )?monitor",
    re.IGNORECASE,
)
CAPTURE_EVENTS = set(REQUIRED_EVENTS[1:]) | FORBIDDEN_EVENTS
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
    """Raised when a mixed-layout E2E log does not prove the contract."""


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


def _one_record(records: list[Record], name: str) -> Record:
    matches = [record for record in records if record[1].get("event") == name]
    if len(matches) != 1:
        raise EvidenceError(
            f"expected exactly one {name!r} event, found {len(matches)}"
        )
    return matches[0]


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
    if output_bound != (480, 480):
        raise EvidenceError(
            "native editor output bound differs from the mixed outputs"
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


def _require_number(value: Any, label: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise EvidenceError(f"{label} must be a finite number")
    return value


def _require_exact_scale(value: Any, expected: float | int, label: str) -> None:
    if _require_number(value, label) != expected:
        raise EvidenceError(f"{label} must equal {expected}")


def _require_geometry(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    geometry = {
        "x": _require_int(value.get("x"), f"{label}.x"),
        "y": _require_int(value.get("y"), f"{label}.y"),
        "width": _require_positive_int(value.get("width"), f"{label}.width"),
        "height": _require_positive_int(value.get("height"), f"{label}.height"),
    }
    return geometry


def _require_exact_geometry(
    value: Any, expected: dict[str, int], label: str
) -> dict[str, int]:
    geometry = _require_geometry(value, label)
    if geometry != expected:
        raise EvidenceError(f"{label} is not the exact mixed-layout rectangle")
    return geometry


def _require_monitor_layout(
    value: Any,
    label: str,
    expected: list[dict[str, Any]] = EXPECTED_MONITOR_LAYOUT,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(expected):
        raise EvidenceError(f"{label} must contain exactly four monitor views")
    layout: list[dict[str, Any]] = []
    for position, rectangle in enumerate(value):
        if not isinstance(rectangle, dict):
            raise EvidenceError(f"{label}[{position}] must be an object")
        normalized = {
            "index": _require_int(rectangle.get("index"), f"{label}[{position}].index"),
            "x": _require_int(rectangle.get("x"), f"{label}[{position}].x"),
            "y": _require_int(rectangle.get("y"), f"{label}[{position}].y"),
            "width": _require_positive_int(
                rectangle.get("width"), f"{label}[{position}].width"
            ),
            "height": _require_positive_int(
                rectangle.get("height"), f"{label}[{position}].height"
            ),
            "scale": _require_number(
                rectangle.get("scale"), f"{label}[{position}].scale"
            ),
        }
        layout.append(normalized)
    if layout != expected:
        raise EvidenceError(f"{label} is not the exact expected rotated layout")
    return layout


def _require_stage_view_scales(
    value: Any,
    expected: list[float | int],
    label: str,
) -> list[float | int]:
    if not isinstance(value, list) or len(value) != len(expected):
        raise EvidenceError(f"{label} must contain exactly four stage-view scales")
    scales = [
        _require_number(scale, f"{label}[{position}]")
        for position, scale in enumerate(value)
    ]
    if scales != expected:
        raise EvidenceError(f"{label} does not match the expected compositor views")
    return scales


def _calculated_topology_id(
    generation: int,
    *,
    stage_width: int = EXPECTED_STAGE_WIDTH,
    stage_height: int = EXPECTED_STAGE_HEIGHT,
    monitor_layout: list[dict[str, Any]] = EXPECTED_MONITOR_LAYOUT,
    stage_view_scales: list[float | int] = EXPECTED_STAGE_VIEW_SCALES,
) -> str:
    signature = json.dumps(
        {
            "version": 1,
            "generation": generation,
            "stage_width": stage_width,
            "stage_height": stage_height,
            "monitors": monitor_layout,
            "stage_view_scales": stage_view_scales,
        },
        separators=(",", ":"),
    )
    value = 0x811C9DC5
    for character in signature:
        value ^= ord(character)
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"v1-{value:08x}"


def _require_topology_id(
    value: Any,
    generation: int,
    label: str,
    **topology: Any,
) -> str:
    expected = _calculated_topology_id(generation, **topology)
    if not isinstance(value, str) or value != expected:
        raise EvidenceError(f"{label} does not bind the exact topology generation")
    return value


def _pixel_dimensions(selection: dict[str, int], scale: float | int) -> tuple[int, int]:
    left = math.floor(selection["x"] * scale)
    top = math.floor(selection["y"] * scale)
    right = math.ceil((selection["x"] + selection["width"]) * scale)
    bottom = math.ceil((selection["y"] + selection["height"]) * scale)
    return right - left, bottom - top


def _intersected_monitor_count(selection: dict[str, int]) -> int:
    return sum(
        selection["x"] < monitor["x"] + monitor["width"]
        and selection["x"] + selection["width"] > monitor["x"]
        and selection["y"] < monitor["y"] + monitor["height"]
        and selection["y"] + selection["height"] > monitor["y"]
        for monitor in EXPECTED_MONITOR_LAYOUT
    )


def _artifact_path(value: Any, basename: str, label: str) -> pathlib.PurePath:
    if not isinstance(value, str) or not value or "\0" in value:
        raise EvidenceError(f"{label} must be a non-empty path")
    path = pathlib.PurePath(value)
    if not path.is_absolute() or path.name != basename:
        raise EvidenceError(f"{label} must be an absolute {basename} path")
    return path


def _validate_encoder_proof(
    proof: dict[str, Any],
    *,
    label: str,
    monitor_layout: list[dict[str, Any]],
    stage_view_scales: list[float | int],
    layout_mode: str,
    stage_width: int,
    stage_height: int,
    capture_scale: float | int,
    texture_width: int,
    texture_height: int,
    selection_geometry: dict[str, int],
    pixel_width_expected: int,
    pixel_height_expected: int,
    png_basename: str,
) -> pathlib.PurePath:
    _require_monitor_layout(
        proof.get("monitor_layout"), f"{label} monitor_layout", monitor_layout
    )
    _require_stage_view_scales(
        proof.get("stage_view_scales"), stage_view_scales,
        f"{label} stage_view_scales"
    )
    if proof.get("capture_layout_mode") != layout_mode:
        raise EvidenceError(f"{label} capture_layout_mode must equal {layout_mode}")
    _require_topology_id(
        proof.get("topology_id"),
        1,
        f"{label} topology_id",
        stage_width=stage_width,
        stage_height=stage_height,
        monitor_layout=monitor_layout,
        stage_view_scales=stage_view_scales,
    )
    _require_exact_scale(
        proof.get("capture_scale"), capture_scale, f"{label} capture_scale"
    )
    if (
        _require_positive_int(proof.get("stage_width"), f"{label} stage_width")
        != stage_width
        or _require_positive_int(proof.get("stage_height"), f"{label} stage_height")
        != stage_height
    ):
        raise EvidenceError(f"{label} stage dimensions are not exact")
    if (
        _require_positive_int(proof.get("texture_width"), f"{label} texture_width")
        != texture_width
        or _require_positive_int(
            proof.get("texture_height"), f"{label} texture_height"
        )
        != texture_height
    ):
        raise EvidenceError(f"{label} texture does not match its stage-scale contract")
    selection = _require_exact_geometry(
        proof.get("selection"), selection_geometry, f"{label} selection"
    )
    expected_pixels = _pixel_dimensions(selection, capture_scale)
    pixel_width = _require_positive_int(
        proof.get("pixel_width"), f"{label} pixel_width"
    )
    pixel_height = _require_positive_int(
        proof.get("pixel_height"), f"{label} pixel_height"
    )
    if (pixel_width, pixel_height) != expected_pixels or expected_pixels != (
        pixel_width_expected,
        pixel_height_expected,
    ):
        raise EvidenceError(f"{label} pixels do not match its scaled selection")
    if (
        _require_positive_int(proof.get("landmarks"), f"{label} landmarks")
        != EXPECTED_LANDMARKS
    ):
        raise EvidenceError(f"{label} did not verify all four monitor landmarks")
    if proof.get("stage_hole_verified") is not True:
        raise EvidenceError(f"{label} did not verify the staggered stage hole")
    return _artifact_path(
        proof.get("png_path"), png_basename, f"{label} png_path"
    )


def _bridge_failure_name(name: Any) -> bool:
    return isinstance(name, str) and (
        name in FORBIDDEN_EVENTS or any(term in name for term in FORBIDDEN_EVENT_TERMS)
    )


def _trace_failure_name(name: Any) -> bool:
    return isinstance(name, str) and (
        name.startswith("portal_")
        or name.startswith("monitor_picker_")
        or name in {"capture_failed", "capture_cancelled", "capture_rejected"}
    )


def validate_log(text: str) -> dict[str, Any]:
    """Validate one exact four-monitor mixed-scale bridge capture."""
    fatal = FATAL_LOG_PATTERN.search(text)
    if fatal is not None:
        raise EvidenceError(f"fatal compositor/test marker present: {fatal.group(0)}")
    picker = RAW_PICKER_PATTERN.search(text)
    if picker is not None:
        raise EvidenceError(f"monitor picker marker present: {picker.group(0)}")

    physical_encoder_records = _records_after_marker(
        text, PHYSICAL_ENCODER_MARKER
    )
    if len(physical_encoder_records) != 1:
        raise EvidenceError(
            "expected exactly one physical encoder proof, found "
            f"{len(physical_encoder_records)}"
        )
    physical_encoder_line, physical_encoder_proof = physical_encoder_records[0]
    physical_encoder_path = _validate_encoder_proof(
        physical_encoder_proof,
        label="physical encoder proof",
        monitor_layout=EXPECTED_PHYSICAL_MONITOR_LAYOUT,
        stage_view_scales=EXPECTED_PHYSICAL_STAGE_VIEW_SCALES,
        layout_mode="physical",
        stage_width=EXPECTED_PHYSICAL_STAGE_WIDTH,
        stage_height=EXPECTED_PHYSICAL_STAGE_HEIGHT,
        capture_scale=EXPECTED_PHYSICAL_CAPTURE_SCALE,
        texture_width=EXPECTED_PHYSICAL_TEXTURE_WIDTH,
        texture_height=EXPECTED_PHYSICAL_TEXTURE_HEIGHT,
        selection_geometry=EXPECTED_PHYSICAL_ENCODER_SELECTION,
        pixel_width_expected=EXPECTED_PHYSICAL_ENCODER_PIXEL_WIDTH,
        pixel_height_expected=EXPECTED_PHYSICAL_ENCODER_PIXEL_HEIGHT,
        png_basename="physical-selected.png",
    )

    encoder_records = _records_after_marker(text, ENCODER_MARKER)
    if len(encoder_records) != 1:
        raise EvidenceError(
            f"expected exactly one mixed encoder proof, found {len(encoder_records)}"
        )
    encoder_line, encoder_proof = encoder_records[0]
    encoder_path = _validate_encoder_proof(
        encoder_proof,
        label="mixed encoder proof",
        monitor_layout=EXPECTED_MONITOR_LAYOUT,
        stage_view_scales=EXPECTED_STAGE_VIEW_SCALES,
        layout_mode="logical",
        stage_width=EXPECTED_STAGE_WIDTH,
        stage_height=EXPECTED_STAGE_HEIGHT,
        capture_scale=EXPECTED_CAPTURE_SCALE,
        texture_width=EXPECTED_TEXTURE_WIDTH,
        texture_height=EXPECTED_TEXTURE_HEIGHT,
        selection_geometry=EXPECTED_ENCODER_SELECTION,
        pixel_width_expected=EXPECTED_ENCODER_PIXEL_WIDTH,
        pixel_height_expected=EXPECTED_ENCODER_PIXEL_HEIGHT,
        png_basename="mixed-selected.png",
    )
    if physical_encoder_line >= encoder_line:
        raise EvidenceError(
            "physical encoder proof did not precede the logical encoder proof"
        )

    event_records = _records_after_marker(text, BRIDGE_MARKER)
    if not event_records:
        raise EvidenceError("no structured bridge events found")
    events = [record[1] for record in event_records]
    if any(event.get("component") != "snipsnap-shell-bridge" for event in events):
        raise EvidenceError("bridge event has an unexpected component")

    pass_records = _records_after_marker(text, PASS_MARKER)
    if len(pass_records) != 1:
        raise EvidenceError(
            f"expected exactly one MIXED-E2E-PASS object, found {len(pass_records)}"
        )
    pass_line, result = pass_records[0]
    if any(
        line_number > pass_line and event.get("event") in CAPTURE_EVENTS
        for line_number, event in event_records
    ):
        raise EvidenceError("capture lifecycle event appears after MIXED-E2E-PASS")

    previous_monotonic_us = 0
    for event in events:
        monotonic_us = _require_positive_int(
            event.get("monotonic_us"), f"{event.get('event')!r} monotonic_us"
        )
        if monotonic_us < previous_monotonic_us:
            raise EvidenceError("bridge monotonic_us values decrease")
        previous_monotonic_us = monotonic_us
        if _bridge_failure_name(event.get("event")):
            raise EvidenceError(f"forbidden bridge event present: {event.get('event')}")

    required_records = [_one_record(event_records, name) for name in REQUIRED_EVENTS]
    required_lines = [record[0] for record in required_records]
    if required_lines != sorted(required_lines):
        raise EvidenceError("required bridge events are out of order")
    if encoder_line >= required_records[1][0]:
        raise EvidenceError("encoder proofs did not precede the real trigger")
    required = [record[1] for record in required_records]

    trigger = required[1]
    capture_id = _require_positive_int(trigger.get("capture_id"), "capture_id")
    if capture_id != EXPECTED_CAPTURE_ID:
        raise EvidenceError("bridge capture_id is not the mixed-test capture")
    for event in events:
        name = event.get("event")
        if name not in CAPTURE_EVENTS:
            continue
        if (
            _require_positive_int(event.get("capture_id"), f"event {name!r} capture_id")
            != capture_id
        ):
            raise EvidenceError(f"event {name!r} has a mismatched capture_id")

    timed = {
        event["event"]: event for event in required if event["event"] in TIMED_EVENTS
    }
    elapsed_values = [
        _require_positive_int(timed[name].get("elapsed_us"), f"{name} elapsed_us")
        for name in TIMED_EVENTS
    ]
    if elapsed_values != sorted(elapsed_values):
        raise EvidenceError("bridge elapsed_us values decrease across the lifecycle")

    capture_ready = required[2]
    if (
        _require_positive_int(capture_ready.get("monitors"), "capture-ready monitors")
        != 4
    ):
        raise EvidenceError("capture-ready must report exactly four monitors")
    stage_width = _require_positive_int(
        capture_ready.get("stage_width"), "capture-ready stage_width"
    )
    stage_height = _require_positive_int(
        capture_ready.get("stage_height"), "capture-ready stage_height"
    )
    if (stage_width, stage_height) != (
        EXPECTED_STAGE_WIDTH,
        EXPECTED_STAGE_HEIGHT,
    ):
        raise EvidenceError("capture-ready stage is not exactly 2560x800")
    _require_exact_scale(
        capture_ready.get("scale"), EXPECTED_CAPTURE_SCALE, "capture-ready scale"
    )
    _require_exact_scale(
        capture_ready.get("capture_scale"),
        EXPECTED_CAPTURE_SCALE,
        "capture-ready capture_scale",
    )
    _require_stage_view_scales(
        capture_ready.get("stage_view_scales"),
        EXPECTED_STAGE_VIEW_SCALES,
        "capture-ready stage_view_scales",
    )
    if capture_ready.get("capture_layout_mode") != "logical":
        raise EvidenceError("capture-ready capture_layout_mode must equal logical")
    if (
        _require_positive_int(
            capture_ready.get("texture_width"), "capture-ready texture_width"
        )
        != EXPECTED_TEXTURE_WIDTH
        or _require_positive_int(
            capture_ready.get("texture_height"), "capture-ready texture_height"
        )
        != EXPECTED_TEXTURE_HEIGHT
    ):
        raise EvidenceError("capture-ready texture is not the max-scale-2 stage")
    _require_monitor_layout(capture_ready.get("monitor_layout"), "capture-ready layout")
    topology_generation = _require_positive_int(
        capture_ready.get("topology_generation"),
        "capture-ready topology_generation",
    )
    topology_id = _require_topology_id(
        capture_ready.get("topology_id"),
        topology_generation,
        "capture-ready topology_id",
    )

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
    if first_paint.get("topology_id") != topology_id:
        raise EvidenceError("first-paint topology differs from capture-ready")

    selection_started = required[4]
    selection_start_x = _require_int(selection_started.get("x"), "selection-started x")
    selection_start_y = _require_int(selection_started.get("y"), "selection-started y")

    selection = required[5]
    selection_geometry = _require_geometry(selection, "bridge selection")
    _require_exact_scale(
        selection.get("scale"), EXPECTED_CAPTURE_SCALE, "selection scale"
    )
    if selection.get("topology_id") != topology_id:
        raise EvidenceError("selection topology differs from capture-ready")
    if (
        selection_start_x != selection_geometry["x"]
        or selection_start_y != selection_geometry["y"]
    ):
        raise EvidenceError("selection-started does not bind the selected rectangle")
    if (
        selection_geometry["x"] + selection_geometry["width"] > stage_width
        or selection_geometry["y"] + selection_geometry["height"] > stage_height
        or selection_geometry["width"] >= stage_width
        or selection_geometry["height"] >= stage_height
    ):
        raise EvidenceError("mixed selection must be a bounded partial-stage rectangle")
    if _intersected_monitor_count(selection_geometry) != 4:
        raise EvidenceError("mixed selection does not intersect all four monitor views")

    handoff_started = required[6]
    if (
        _require_geometry(handoff_started, "handoff-started geometry")
        != selection_geometry
    ):
        raise EvidenceError("handoff-started geometry differs from selection")
    _require_exact_scale(
        handoff_started.get("scale"),
        EXPECTED_CAPTURE_SCALE,
        "handoff-started scale",
    )
    if handoff_started.get("topology_id") != topology_id:
        raise EvidenceError("handoff-started topology differs from capture-ready")

    encoded = required[7]
    pixel_width = _require_positive_int(
        encoded.get("pixel_width"), "handoff pixel_width"
    )
    pixel_height = _require_positive_int(
        encoded.get("pixel_height"), "handoff pixel_height"
    )
    expected_pixels = _pixel_dimensions(selection_geometry, EXPECTED_CAPTURE_SCALE)
    if (pixel_width, pixel_height) != expected_pixels:
        raise EvidenceError(
            "encoded pixel dimensions differ from the scale-2 selection"
        )
    png_bytes = _require_positive_int(
        encoded.get("png_bytes"), "handoff PNG byte count"
    )
    if encoded.get("topology_id") != topology_id:
        raise EvidenceError("handoff-encoded topology differs from capture-ready")

    observed_window = required[8]
    daemon_pid = _require_positive_int(
        observed_window.get("daemon_pid"), "observed editor daemon_pid"
    )
    expected_title = f"SnipSnap [capture-id={capture_id}]"
    if observed_window.get("title") != expected_title:
        raise EvidenceError("observed editor title does not bind the capture ID")
    committed = required[9]
    if (
        _require_positive_int(committed.get("daemon_pid"), "commit daemon_pid")
        != daemon_pid
    ):
        raise EvidenceError("commit daemon_pid differs from the observed editor")
    overlay_closed = required[10]
    if overlay_closed.get("reason") != "commit-accepted":
        raise EvidenceError("overlay did not close because the commit was accepted")
    focus_requested = required[11]
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
    for trace in traces:
        if _trace_failure_name(trace.get("event")):
            raise EvidenceError(
                f"forbidden native trace event present: {trace.get('event')}"
            )
    required_trace_records = [
        _one_record(trace_records, name) for name in REQUIRED_TRACE_EVENTS
    ]
    trace_lines = [record[0] for record in required_trace_records]
    if trace_lines != sorted(trace_lines):
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
    requested = required_trace_records[0][1]
    if requested.get("elapsed_us") != 0:
        raise EvidenceError("native capture_requested must begin at elapsed_us=0")
    requested_fields = requested.get("fields")
    if (
        not isinstance(requested_fields, dict)
        or requested_fields.get("source") != "gnome_shell_bridge"
        or requested_fields.get("origin") != "request_receipt"
    ):
        raise EvidenceError("native trace does not identify bridge request receipt")
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

    if pass_line <= required_records[-1][0]:
        raise EvidenceError("MIXED-E2E-PASS precedes the completed bridge lifecycle")
    if pass_line <= required_trace_records[-1][0]:
        raise EvidenceError("MIXED-E2E-PASS precedes the native commit trace")
    if pass_line <= navigation_last_line:
        raise EvidenceError(
            "MIXED-E2E-PASS precedes completed editor navigation"
        )
    if pass_line <= encoder_line:
        raise EvidenceError("MIXED-E2E-PASS precedes the encoder proof")
    if pass_line <= physical_encoder_line:
        raise EvidenceError("MIXED-E2E-PASS precedes the physical encoder proof")
    if result.get("event") != "passed":
        raise EvidenceError("MIXED-E2E-PASS does not report event=passed")
    if (
        _require_positive_int(result.get("capture_id"), "MIXED-E2E-PASS capture_id")
        != capture_id
    ):
        raise EvidenceError("MIXED-E2E-PASS capture_id differs from bridge lifecycle")
    if (
        _require_positive_int(result.get("daemon_pid"), "MIXED-E2E-PASS daemon_pid")
        != daemon_pid
    ):
        raise EvidenceError("MIXED-E2E-PASS daemon_pid differs from observed editor")
    if _require_positive_int(result.get("monitors"), "MIXED-E2E-PASS monitors") != 4:
        raise EvidenceError("MIXED-E2E-PASS must report exactly four monitors")
    if (
        _require_positive_int(result.get("stage_width"), "MIXED-E2E-PASS stage_width")
        != stage_width
        or _require_positive_int(
            result.get("stage_height"), "MIXED-E2E-PASS stage_height"
        )
        != stage_height
    ):
        raise EvidenceError("MIXED-E2E-PASS stage differs from capture-ready")
    _require_monitor_layout(result.get("monitor_layout"), "MIXED-E2E-PASS layout")
    if result.get("selection") != selection_geometry:
        raise EvidenceError("MIXED-E2E-PASS selection differs from bridge selection")
    if (
        _require_positive_int(
            result.get("selection_spans_monitors"), "selection_spans_monitors"
        )
        != 4
    ):
        raise EvidenceError("mixed selection did not intersect all four monitors")
    if result.get("encoder_proof") != encoder_proof:
        raise EvidenceError(
            "MIXED-E2E-PASS encoder proof differs from standalone proof"
        )
    if result.get("physical_encoder_proof") != physical_encoder_proof:
        raise EvidenceError(
            "MIXED-E2E-PASS physical encoder proof differs from standalone proof"
        )
    for field in (
        "editor_normal",
        "editor_focused",
        "editor_showing",
        "editor_mapped",
        "overlay_closed",
    ):
        if result.get(field) is not True:
            raise EvidenceError(f"MIXED-E2E-PASS must report {field}=true")
    if result.get("editor_title") != expected_title:
        raise EvidenceError("MIXED-E2E-PASS editor title does not bind capture ID")
    identities = result.get("identities")
    if not isinstance(identities, list) or not all(
        isinstance(identity, str) for identity in identities
    ):
        raise EvidenceError("MIXED-E2E-PASS identities must be a string array")
    if not any(
        identity in {"snipsnap", "tech.norvi.snipsnap"} for identity in identities
    ):
        raise EvidenceError("MIXED-E2E-PASS lacks an accepted editor identity")
    editor_frame = _require_geometry(result.get("editor_frame"), "editor_frame")
    if (editor_frame["width"], editor_frame["height"]) != (
        view_fields["window_width"],
        view_fields["window_height"],
    ):
        raise EvidenceError("editor_frame differs from the native editor window trace")
    if (
        editor_frame["x"] + editor_frame["width"] > stage_width
        or editor_frame["y"] + editor_frame["height"] > stage_height
    ):
        raise EvidenceError("editor_frame is outside the compositor stage")
    if result.get("editor_landmarks_verified") is not True:
        raise EvidenceError(
            "MIXED-E2E-PASS must verify captured landmarks in the editor"
        )
    if result.get("helper_window_removed") is not True:
        raise EvidenceError(
            "MIXED-E2E-PASS must remove test chrome before pixel verification"
        )
    overlay_path = _artifact_path(
        result.get("overlay_screenshot"),
        "mixed-overlay.png",
        "overlay_screenshot",
    )
    editor_path = _artifact_path(
        result.get("editor_screenshot"),
        "mixed-editor.png",
        "editor_screenshot",
    )
    one_to_one_path = _artifact_path(
        result.get("editor_one_to_one_screenshot"),
        "mixed-editor-one-to-one.png",
        "editor_one_to_one_screenshot",
    )
    if not (
        physical_encoder_path.parent
        == encoder_path.parent
        == overlay_path.parent
        == editor_path.parent
        == one_to_one_path.parent
    ):
        raise EvidenceError("mixed E2E artifacts do not share one output directory")

    return {
        "capture_id": capture_id,
        "native_capture_id": native_capture_id,
        "daemon_pid": daemon_pid,
        "monitors": 4,
        "stage_width": stage_width,
        "stage_height": stage_height,
        "capture_scale": EXPECTED_CAPTURE_SCALE,
        "texture_width": EXPECTED_TEXTURE_WIDTH,
        "texture_height": EXPECTED_TEXTURE_HEIGHT,
        "topology_id": topology_id,
        "topology_generation": topology_generation,
        "encoder_topology_id": encoder_proof["topology_id"],
        "physical_encoder_topology_id": physical_encoder_proof["topology_id"],
        "physical_capture_scale": EXPECTED_PHYSICAL_CAPTURE_SCALE,
        "physical_texture_width": EXPECTED_PHYSICAL_TEXTURE_WIDTH,
        "physical_texture_height": EXPECTED_PHYSICAL_TEXTURE_HEIGHT,
        "selection_width": selection_geometry["width"],
        "selection_height": selection_geometry["height"],
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "png_bytes": png_bytes,
        "landmarks": EXPECTED_LANDMARKS,
        "stage_hole_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=pathlib.Path, help="combined mixed GNOME test log")
    args = parser.parse_args()

    try:
        summary = validate_log(args.log.read_text(encoding="utf-8"))
    except (EvidenceError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
