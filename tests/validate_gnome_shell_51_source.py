#!/usr/bin/env python3
"""Check SnipSnap's Shell bridge APIs against pinned GNOME 51 sources.

This is deliberately a source compatibility gate, not a substitute for the
real compositor E2E run. It gives the development-release metadata an audited
upstream basis and fails if a Shell or Mutter update removes an API that the
bridge uses before a GNOME 51 runtime is available in the Ubuntu CI image.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class CompatibilityError(RuntimeError):
    pass


def read_source(root: Path, relative: str) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CompatibilityError(f"cannot read {path}: {error}") from error


def require_text(source: str, expected: str, location: str) -> None:
    if expected not in source:
        raise CompatibilityError(f"{location} is missing {expected!r}")


def require_pattern(source: str, pattern: str, location: str) -> None:
    if re.search(pattern, source, re.MULTILINE) is None:
        raise CompatibilityError(f"{location} does not match {pattern!r}")


def validate_shell(shell: Path) -> None:
    meson = read_source(shell, "meson.build")
    require_text(meson, "mutter_api_version = '51'", "gnome-shell/meson.build")
    require_text(meson, "mutter_req = '>= 51.beta'", "gnome-shell/meson.build")

    screenshot = read_source(shell, "js/ui/screenshot.js")
    for expected in (
        "Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');",
        "Gio._promisify(Shell.Screenshot, 'composite_to_stream');",
        "await this._shooter.screenshot_stage_to_content();",
        "Main.layoutManager.emit('system-modal-opened');",
        "const {screenshotUIGroup} = Main.layoutManager;",
    ):
        require_text(screenshot, expected, "gnome-shell/js/ui/screenshot.js")

    main = read_source(shell, "js/ui/main.js")
    for exported_function in ("pushModal", "popModal", "activateWindow"):
        require_pattern(
            main,
            rf"^export function {exported_function}\(",
            "gnome-shell/js/ui/main.js",
        )

    window_manager = read_source(shell, "js/ui/windowManager.js")
    for signature in (
        "addKeybinding(name, settings, flags, modes, handler)",
        "removeKeybinding(name)",
    ):
        require_text(
            window_manager, signature, "gnome-shell/js/ui/windowManager.js"
        )

    extension_system = read_source(shell, "js/ui/extensionSystem.js")
    require_text(
        extension_system,
        "const [major] = Config.PACKAGE_VERSION.split('.');",
        "gnome-shell/js/ui/extensionSystem.js",
    )
    require_text(
        extension_system,
        ".some(v => v.startsWith(major));",
        "gnome-shell/js/ui/extensionSystem.js",
    )

    session_mode = read_source(shell, "js/ui/sessionMode.js")
    for expected in (
        "get currentMode()",
        "parentMode:",
        "hasWindows:",
        "isLocked:",
        "isGreeter:",
        "this.emit('updated');",
    ):
        require_text(session_mode, expected, "gnome-shell/js/ui/sessionMode.js")

    layout = read_source(shell, "js/ui/layout.js")
    for expected in (
        "this.screenshotUIGroup = new St.Widget({",
        "'system-modal-opened': {}",
        "'monitors-changed': {}",
        "this.monitors = [];",
    ):
        require_text(layout, expected, "gnome-shell/js/ui/layout.js")

    looking_glass = read_source(shell, "js/ui/lookingGlass.js")
    require_text(
        looking_glass,
        "global.display.connect('window-created'",
        "gnome-shell/js/ui/lookingGlass.js",
    )

    quick_settings = read_source(shell, "js/ui/quickSettings.js")
    require_text(
        quick_settings,
        "const laters = global.compositor.get_laters();",
        "gnome-shell/js/ui/quickSettings.js",
    )
    require_text(
        quick_settings,
        "Meta.LaterType.BEFORE_REDRAW",
        "gnome-shell/js/ui/quickSettings.js",
    )

    screenshot_header = read_source(shell, "src/shell-screenshot.h")
    for symbol in (
        "shell_screenshot_screenshot_stage_to_content",
        "shell_screenshot_composite_to_stream",
    ):
        require_text(screenshot_header, symbol, "gnome-shell/src/shell-screenshot.h")

    screenshot_source = read_source(shell, "src/shell-screenshot.c")
    for expected in (
        "clutter_stage_get_capture_final_size (stage, &screenshot_rect,",
        "content = clutter_stage_paint_to_content (stage, &screenshot_rect, scale,",
        "COGL_PIXEL_FORMAT_ARGB32_NATIVE",
    ):
        require_text(
            screenshot_source, expected, "gnome-shell/src/shell-screenshot.c"
        )

    action_modes = read_source(shell, "src/shell-action-modes.h")
    for symbol in (
        "SHELL_ACTION_MODE_LOGIN_SCREEN",
        "SHELL_ACTION_MODE_LOCK_SCREEN",
        "SHELL_ACTION_MODE_UNLOCK_SCREEN",
        "SHELL_ACTION_MODE_POPUP",
        "SHELL_ACTION_MODE_ALL",
    ):
        require_text(action_modes, symbol, "gnome-shell/src/shell-action-modes.h")


def validate_mutter(mutter: Path) -> None:
    meson = read_source(mutter, "meson.build")
    require_pattern(
        meson,
        r"^project\(\s*'mutter',\s*'c',\s*version:\s*'51\.beta'",
        "mutter/meson.build",
    )

    clutter_mutter = read_source(mutter, "clutter/clutter/clutter-mutter.h")
    require_text(
        clutter_mutter,
        "clutter_stage_peek_stage_views (ClutterStage *stage)",
        "mutter/clutter/clutter/clutter-mutter.h",
    )

    stage_view = read_source(mutter, "clutter/clutter/clutter-stage-view.h")
    require_text(
        stage_view,
        "clutter_stage_view_get_scale (ClutterStageView *view)",
        "mutter/clutter/clutter/clutter-stage-view.h",
    )

    stage_source = read_source(mutter, "clutter/clutter/clutter-stage.c")
    for expected in (
        "clutter_stage_get_capture_final_size (ClutterStage *stage,",
        "max_scale = MAX (clutter_stage_view_get_scale (view), max_scale);",
        "roundf (rect->width * max_scale)",
        "roundf (rect->height * max_scale)",
    ):
        require_text(
            stage_source, expected, "mutter/clutter/clutter/clutter-stage.c"
        )

    actor = read_source(mutter, "clutter/clutter/clutter-actor.c")
    require_text(actor, 'g_signal_new (I_("captured-event")', "clutter-actor.c")

    event = read_source(mutter, "clutter/clutter/clutter-event.h")
    for symbol in (
        "clutter_event_type",
        "clutter_event_get_coords",
        "clutter_event_get_key_symbol",
        "clutter_event_get_button",
    ):
        require_text(event, symbol, "mutter/clutter/clutter/clutter-event.h")

    later = read_source(mutter, "src/meta/meta-later.h")
    require_text(later, "META_LATER_BEFORE_REDRAW", "mutter/src/meta/meta-later.h")
    require_text(later, "meta_laters_add", "mutter/src/meta/meta-later.h")

    window = read_source(mutter, "src/meta/window.h")
    for symbol in (
        "meta_window_get_window_type",
        "meta_window_get_compositor_private",
        "meta_window_get_title",
        "meta_window_get_pid",
    ):
        require_text(window, symbol, "mutter/src/meta/window.h")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gnome-shell", type=Path, required=True)
    parser.add_argument("--mutter", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        validate_shell(arguments.gnome_shell.resolve(strict=True))
        validate_mutter(arguments.mutter.resolve(strict=True))
    except (OSError, CompatibilityError) as error:
        print(f"GNOME 51 source compatibility failed: {error}", file=sys.stderr)
        return 1
    print("validated SnipSnap bridge APIs against GNOME Shell/Mutter 51.beta")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
