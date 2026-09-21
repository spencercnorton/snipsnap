#!/usr/bin/env python3
"""Static policy checks for the GNOME Shell capture bridge."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


EXPECTED_UUID = "snipsnap-shell-bridge@kleos.norvi.tech"
EXPECTED_SOURCE_DIRECTORY = "snipsnap-shell-bridge"
EXPECTED_SCHEMA = "org.gnome.shell.extensions.flameshot-shell-bridge"
EXPECTED_SCHEMA_PATH = "/org/gnome/shell/extensions/flameshot-shell-bridge/"
EXPECTED_SHELL_VERSIONS = ["50", "51"]
FORBIDDEN_JAVASCRIPT_PATTERNS = {
    r"\bimports\.(?:misc|ui)\b": "legacy pre-GNOME-45 imports",
    r"\bextensionUtils\b": "legacy ExtensionUtils API",
    r"\beval\s*\(": "dynamic eval",
    r"\bnew\s+Function\s*\(": "dynamic Function construction",
    r"\bGio\.DBusExportedObject\b": "exported D-Bus object",
    r"\bGio\.DBus(?:Connection|Proxy)?\b": "D-Bus API",
    r"\bGio\.bus_own_name\b": "owned D-Bus service name",
    r"\bGio\.File\b": "filesystem access from GNOME Shell",
    r"\bGio\.Subprocess\b": "subprocess execution from GNOME Shell",
    r"\bGio\.(?:InetAddress|InetSocketAddress|NetworkAddress|NetworkService|ProxyAddress|Resolver)\b": "network socket address",
    r"\bGio\.(?:SocketListener|SocketService)\b": "socket listener",
    r"(?:\bnew\s+Gio\.Socket\b|\bGio\.Socket\.(?:new|new_from_fd)\b)": "raw socket construction",
    r"\bconnect_to_(?:host|service|uri)\b": "remote socket connection",
    r"gi://Soup": "HTTP client API",
    r"\bGLib\.(?:file_(?:get|set)_contents|open|mkdir|mkdtemp|unlink|rename|chmod|chown)\b": "filesystem access from GNOME Shell",
    r"\bGLib\.spawn(?:_async|_sync)?\b": "process spawning from GNOME Shell",
    r"\bMain\.notify\b": "desktop notification containing capture state",
    r"org\.freedesktop\.portal\.Screenshot": "screenshot portal call",
    r"\bcaptureScreenshot\s*\(": "disk-backed GNOME screenshot helper",
}


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"metadata.json contains duplicate key {key!r}")
        result[key] = value
    return result


def load_metadata(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"missing required file: {path}")
    require(path.stat().st_size <= 64 * 1024, "metadata.json is unexpectedly large")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"unable to parse metadata.json: {error}") from error
    require(isinstance(value, dict), "metadata.json must contain a JSON object")
    return value


def load_json_object(path: Path, description: str) -> dict[str, Any]:
    require(path.is_file(), f"missing required file: {path}")
    require(path.stat().st_size <= 64 * 1024, f"{description} is unexpectedly large")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"unable to parse {description}: {error}") from error
    require(isinstance(value, dict), f"{description} must contain a JSON object")
    return value


def validate_tree(root: Path) -> None:
    require(root.is_dir(), f"extension source directory does not exist: {root}")
    require(
        root.name == EXPECTED_SOURCE_DIRECTORY,
        "extension source directory must be named "
        f"{EXPECTED_SOURCE_DIRECTORY}",
    )

    forbidden_suffixes = {
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".jar",
        ".node",
        ".pyc",
        ".so",
        ".wasm",
    }
    files = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        require(
            not any(part.startswith(".") for part in relative.parts),
            f"hidden packaging input is not allowed: {relative}",
        )
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), f"symbolic links are not allowed: {relative}")
        require(
            stat.S_ISDIR(mode) or stat.S_ISREG(mode),
            f"special filesystem entry is not allowed: {relative}",
        )
        if not stat.S_ISREG(mode):
            continue
        files += 1
        require(
            not mode & stat.S_IXUSR,
            f"extension files must not be executable: {relative}",
        )
        # Git records only the executable bit, and some container runners expose
        # a checkout through a mode-0666 bind mount. Source write bits therefore
        # are not an attestable repository property. The deterministic packager
        # fixes every archive member at 0644, while the activation manager
        # independently requires the installed tree to be owner-controlled.
        require(
            path.suffix.lower() not in forbidden_suffixes,
            f"compiled executable content is not allowed: {relative}",
        )
        require(
            relative.as_posix() != "schemas/gschemas.compiled",
            "GNOME 44+ packages must ship schema XML, not gschemas.compiled",
        )
    require(files >= 3, "extension source tree is incomplete")


def validate_metadata(root: Path) -> dict[str, Any]:
    metadata = load_metadata(root / "metadata.json")
    for key in ("uuid", "name", "description", "shell-version", "settings-schema"):
        require(key in metadata, f"metadata.json is missing required key {key!r}")

    require(
        metadata["uuid"] == EXPECTED_UUID,
        "metadata UUID does not match the managed extension",
    )
    require(
        isinstance(metadata["name"], str) and metadata["name"].strip(),
        "metadata name must be a non-empty string",
    )
    require(
        isinstance(metadata["description"], str) and metadata["description"].strip(),
        "metadata description must be a non-empty string",
    )
    require(
        metadata["shell-version"] == EXPECTED_SHELL_VERSIONS,
        "bridge must target the audited GNOME Shell 50 and 51 releases",
    )
    require(
        metadata["settings-schema"] == EXPECTED_SCHEMA,
        "metadata settings-schema does not match the managed schema",
    )

    # The default session mode is `user`; spelling it out has the same runtime
    # behavior. Never permit unlock-dialog, gdm, or another privileged mode.
    session_modes = metadata.get("session-modes", ["user"])
    require(
        session_modes == ["user"],
        "the bridge must be restricted to the unlocked user session",
    )
    return metadata


def validate_install_manifest(root: Path, manifest_path: Path) -> list[Path]:
    manifest = load_json_object(manifest_path, "install-manifest.json")
    require(manifest.get("uuid") == EXPECTED_UUID, "install manifest UUID is incorrect")
    require(
        manifest.get("source-directory") == EXPECTED_SOURCE_DIRECTORY,
        "install manifest source directory is incorrect",
    )
    expected_install = f"$XDG_DATA_HOME/gnome-shell/extensions/{EXPECTED_UUID}"
    require(
        manifest.get("install-directory") == expected_install,
        "managed install path must remain user-scoped",
    )

    declared_files = manifest.get("files")
    require(
        isinstance(declared_files, list)
        and all(isinstance(item, str) for item in declared_files),
        "install manifest files must be a string array",
    )
    actual_files = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )
    require(
        sorted(declared_files) == actual_files,
        "install manifest must exactly enumerate the extension payload",
    )
    require(
        manifest.get("generated-files") == ["schemas/gschemas.compiled"],
        "only the installer-generated schema cache may be declared",
    )
    require(
        manifest.get("default-keybinding") != ["Print"],
        "the package must not claim Print before managed activation",
    )
    require(
        manifest.get("managed-desktop-keybinding") == ["Print"],
        "managed desktop activation must remain explicit in the manifest",
    )
    return [Path(item) for item in declared_files]


def validate_schema(root: Path) -> None:
    schema_file = root / "schemas" / f"{EXPECTED_SCHEMA}.gschema.xml"
    require(schema_file.is_file(), f"missing managed GSettings schema: {schema_file}")
    try:
        document = ET.parse(schema_file)
    except (OSError, ET.ParseError) as error:
        raise ValidationError(f"unable to parse GSettings schema: {error}") from error

    schemas = document.getroot().findall("schema")
    matches = [schema for schema in schemas if schema.get("id") == EXPECTED_SCHEMA]
    require(len(matches) == 1, "schema XML must define the managed schema exactly once")
    require(
        matches[0].get("path") == EXPECTED_SCHEMA_PATH,
        "managed schema uses an unexpected GSettings path",
    )


def read_javascript(path: Path, relative: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(
            f"unable to read {relative.as_posix()}: {error}"
        ) from error


def validate_packaged_javascript(root: Path, packaged_files: list[Path]) -> None:
    javascript_files = [path for path in packaged_files if path.suffix.lower() == ".js"]
    require(
        javascript_files, "install manifest must package at least one JavaScript module"
    )
    for relative in javascript_files:
        source = read_javascript(root / relative, relative)
        # The handoff client may make exactly one kind of outbound D-Bus call:
        # resolving the well-known SnipSnap service to its kernel PID. Keep
        # the blanket D-Bus ban effective for every other use and module.
        forbidden_source = source
        if relative.as_posix() == "handoff-client.js":
            forbidden_source = forbidden_source.replace("Gio.DBus.session.call(", "")
        for pattern, description in FORBIDDEN_JAVASCRIPT_PATTERNS.items():
            require(
                re.search(pattern, forbidden_source) is None,
                f"{relative.as_posix()} contains forbidden {description}",
            )

        sensitive_apis = {
            "Gio.SocketClient": "handoff-client.js",
            "Gio.UnixSocketAddress": "handoff-client.js",
            "Gio.MemoryOutputStream": "selected-region.js",
            "Shell.Screenshot.composite_to_stream": "selected-region.js",
        }
        for api, allowed_module in sensitive_apis.items():
            require(
                api not in source or relative.as_posix() == allowed_module,
                f"{relative.as_posix()} may not use sensitive API {api}",
            )


def validate_handoff_javascript(root: Path) -> None:
    client_file = root / "handoff-client.js"
    encoder_file = root / "selected-region.js"
    topology_file = root / "capture-topology.js"
    require(client_file.is_file(), f"missing handoff client: {client_file}")
    require(encoder_file.is_file(), f"missing selected-region encoder: {encoder_file}")
    require(topology_file.is_file(), f"missing capture topology: {topology_file}")
    client = read_javascript(client_file, Path("handoff-client.js"))
    encoder = read_javascript(encoder_file, Path("selected-region.js"))
    topology = read_javascript(topology_file, Path("capture-topology.js"))
    extension = read_javascript(root / "extension.js", Path("extension.js"))
    activation_start = extension.find("    _scheduleEditorActivation(")
    activation_end = extension.find("\n    _cancelEditorActivation(", activation_start)
    require(
        activation_start >= 0 and activation_end > activation_start,
        "extension.js must define a bounded editor activation method",
    )
    activation_method = extension[activation_start:activation_end]

    # Anchor the approved method names to actual call arguments; a bare
    # substring check accepts names demoted to comments while the real calls
    # target something else entirely.
    dbus_calls = re.findall(
        r"Gio\.DBus\.session\.call\("
        r"\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',",
        client,
    )
    require(
        client.count("Gio.DBus.session.call(") == 2 and len(dbus_calls) == 2,
        "handoff-client.js must contain exactly the two approved D-Bus calls: "
        "peer-PID resolution and SnipSnap service activation",
    )
    require(
        all(
            call[:3]
            == ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
            for call in dbus_calls
        )
        and {call[3] for call in dbus_calls}
        == {"GetConnectionUnixProcessID", "StartServiceByName"},
        "handoff-client.js D-Bus calls must target the message bus with exactly "
        "GetConnectionUnixProcessID and StartServiceByName",
    )

    for required in (
        "GLib.get_user_runtime_dir()",
        "gnome-shell-bridge-v1.sock",
        "Gio.UnixSocketAddress.new(",
        "client.set_enable_proxy(false)",
        ".get_credentials()",
        ".get_unix_user()",
        ".get_unix_pid()",
        "tech.norvi.snipsnap",
        "servicePid !== peerPid",
        "ack.daemonPid !== peerPid",
        "encodeCommit(captureId)",
        "AckStatus.COMMIT_ACCEPTED",
        "commitStarted = true",
    ):
        require(required in client, f"handoff-client.js is missing {required!r}")
    require(
        "client.set_enable_proxy(true)" not in client,
        "handoff client must never enable proxy resolution",
    )
    require(
        "Shell.Screenshot.composite_to_stream(" in encoder,
        "selected-region encoder must use the direct compositor API",
    )
    for required in (
        "capture scale differs from the frozen maximum stage view scale",
        "captured texture dimensions differ from the stage-scale contract",
        "stage view scales do not match a GNOME physical/logical layout",
        "capture topology monitors are disconnected",
        "capture topology monitors overlap",
        "whole-stage capture exceeds allocation bounds",
    ):
        require(
            required in topology,
            f"capture-topology.js is missing fail-closed guard {required!r}",
        )
    require(
        "global.stage.peek_stage_views()" in extension
        and ".map(view => Number(view.get_scale()))" in extension,
        "capture topology must snapshot compositor StageView scales directly",
    )
    require(
        "validateCapturedStage(captureTopology, scale, textureSize)" in encoder,
        "selected-region readback must revalidate its immutable stage texture",
    )
    require(
        "logEvent('commit-ack-indeterminate'" in extension,
        "extension must resolve a lost post-commit ACK without retry",
    )
    for required in (
        "selectionEncodeGate.busy",
        "reason: 'selection-encode-in-flight'",
        "selectionEncodeGate.finish(encodeOperation)",
    ):
        require(
            required in extension,
            f"extension.js is missing uncancellable encoder gate {required!r}",
        )
    require(
        "global.display.connect(\n            'window-created'" in extension,
        "handoff focus must observe windows created during the request",
    )
    for required in (
        "window.get_pid()",
        "window.get_window_type()",
        "window.get_title()",
        "Meta.WindowType.NORMAL",
    ):
        require(
            required in extension, f"extension.js is missing focus guard {required!r}"
        )
    for required in (
        "global.compositor.get_laters()",
        "Meta.LaterType.BEFORE_REDRAW",
        "this._matchesEditorWindow(\n                        editorWindow, peerPid, captureId)",
        "if (!this._isUserSession() ||",
        "editorWindow.get_compositor_private()",
        "if (!actor?.mapped)",
        "EDITOR_ACTIVATION_MAX_FRAMES",
        "GLib.SOURCE_CONTINUE",
        "--remainingFrames",
    ):
        require(
            required in activation_method,
            f"extension.js is missing deferred focus guard {required!r}",
        )
    require(
        activation_method.count("Main.activateWindow(editorWindow)") == 1
        and extension.count("Main.activateWindow(editorWindow)") == 1,
        "staged editor activation must occur only in its guarded compositor callback",
    )
    require(
        "if (!this._isUserSession()) {\n"
        "                this._cancelEditorActivation();" in extension,
        "session transitions must cancel deferred editor activation",
    )
    require(
        "cancellable.connect(() => {" in extension
        and "cancellable.connect('cancelled'" not in extension,
        "Gio.Cancellable must use its GNOME 50/51 callback-only connect API",
    )
    state = read_javascript(root / "handoff-state.js", Path("handoff-state.js"))
    require(
        "SnipSnap [capture-id=${value}]" in state,
        "focus guard must bind the editor to its exact capture-ID title",
    )


def validate_extension_javascript(root: Path) -> None:
    extension_file = root / "extension.js"
    require(extension_file.is_file(), f"missing required file: {extension_file}")
    source = read_javascript(extension_file, Path("extension.js"))

    require(
        "resource:///org/gnome/shell/extensions/extension.js" in source,
        "extension.js must use the GNOME 45+ ESM Extension base class",
    )
    require(
        re.search(r"^\s*export\s+default\s+class\b", source, re.MULTILINE) is not None,
        "extension.js must export a default ESM extension class",
    )
    require(
        re.search(r"\benable\s*\(", source) is not None,
        "extension.js must implement enable()",
    )
    require(
        re.search(r"\bdisable\s*\(", source) is not None,
        "extension.js must implement disable() for rollback and lock transitions",
    )
    for guard in (
        "Main.sessionMode.hasWindows",
        "Main.sessionMode.isLocked",
        "Main.sessionMode.isGreeter",
        "Main.sessionMode.connect('updated'",
    ):
        require(guard in source, f"extension.js is missing session guard {guard!r}")
    require(
        "parentMode === 'user'" in source,
        "extension.js must accept 'user'-derived session modes (e.g. Ubuntu's "
        "'ubuntu', whose parentMode is 'user'); gating on currentMode === 'user' "
        "alone refuses every capture on Ubuntu.",
    )
    require(
        "screenshot_stage_to_content()" in source,
        "GNOME 50/51 bridge must use compositor-local stage capture",
    )


def validate(root: Path, manifest_path: Path | None = None) -> None:
    root = root.resolve(strict=True)
    manifest_path = (
        manifest_path
        if manifest_path is not None
        else root.parent / "install-manifest.json"
    ).resolve(strict=True)
    validate_tree(root)
    validate_metadata(root)
    packaged_files = validate_install_manifest(root, manifest_path)
    validate_schema(root)
    validate_packaged_javascript(root, packaged_files)
    validate_extension_javascript(root)
    validate_handoff_javascript(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("extension_dir", type=Path)
    arguments = parser.parse_args()
    try:
        validate(arguments.extension_dir, arguments.manifest)
    except (OSError, ValidationError) as error:
        print(f"GNOME Shell extension validation failed: {error}", file=sys.stderr)
        return 1
    print(f"validated GNOME Shell 50 and 51 extension: {EXPECTED_UUID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
