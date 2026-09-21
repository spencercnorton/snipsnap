#!/usr/bin/env python3
"""Unit tests for the user-scoped GNOME Shell bridge activation helper."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "packaging" / "gnome-shell" / "manage_shell_bridge.py"
SPEC = importlib.util.spec_from_file_location("manage_shell_bridge", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class MemoryStateStore:
    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None
        self.save_count = 0

    @property
    def location(self) -> str:
        return "memory://activation-state"

    def load(self) -> dict[str, Any] | None:
        return copy.deepcopy(self.state)

    def save(self, state: Mapping[str, Any]) -> None:
        self.state = copy.deepcopy(dict(state))
        self.save_count += 1


class FakeCommandExecutor:
    def __init__(self) -> None:
        custom_ref = bridge.GSettingsBackend.schema_reference(
            bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
        )
        self.values = {
            (bridge.CUSTOM_REGISTRY_SCHEMA, bridge.CUSTOM_REGISTRY_KEY): (
                f"['{bridge.CUSTOM_PATH}']"
            ),
            (custom_ref, "binding"): "'Print'",
            (custom_ref, "command"): "'snipsnap gui'",
            (custom_ref, "name"): "'SnipSnap'",
            (bridge.SHELL_SCHEMA, "show-screenshot-ui"): ("['Print', '<Super>Print']"),
            (bridge.SHELL_SCHEMA, "screenshot"): "[]",
            (bridge.SHELL_SCHEMA, "screenshot-window"): "['<Alt>Print']",
            (bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY): ("['<Super><Shift>s']"),
            (bridge.BRIDGE_SCHEMA, bridge.ANNOTATOR_KEY): "false",
        }
        self.path_to_setting = {
            f"{bridge.CUSTOM_PATH}binding": (custom_ref, "binding"),
            f"{bridge.CUSTOM_PATH}command": (custom_ref, "command"),
            f"{bridge.CUSTOM_PATH}name": (custom_ref, "name"),
            f"{bridge.SHELL_DCONF_ROOT}/show-screenshot-ui": (
                bridge.SHELL_SCHEMA,
                "show-screenshot-ui",
            ),
            f"{bridge.SHELL_DCONF_ROOT}/screenshot": (
                bridge.SHELL_SCHEMA,
                "screenshot",
            ),
            f"{bridge.SHELL_DCONF_ROOT}/screenshot-window": (
                bridge.SHELL_SCHEMA,
                "screenshot-window",
            ),
            bridge.BRIDGE_DCONF_PATH: (bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY),
            bridge.ANNOTATOR_DCONF_PATH: (
                bridge.BRIDGE_SCHEMA,
                bridge.ANNOTATOR_KEY,
            ),
        }
        self.defaults = {
            f"{bridge.CUSTOM_PATH}binding": "''",
            f"{bridge.CUSTOM_PATH}command": "''",
            f"{bridge.CUSTOM_PATH}name": "''",
            f"{bridge.SHELL_DCONF_ROOT}/show-screenshot-ui": "[]",
            f"{bridge.SHELL_DCONF_ROOT}/screenshot": "[]",
            f"{bridge.SHELL_DCONF_ROOT}/screenshot-window": "['<Alt>Print']",
            bridge.BRIDGE_DCONF_PATH: "['<Super><Shift>s']",
            bridge.ANNOTATOR_DCONF_PATH: "false",
        }
        self.overrides = {
            f"{bridge.CUSTOM_PATH}binding": "'Print'",
            f"{bridge.CUSTOM_PATH}command": "'snipsnap gui'",
            f"{bridge.CUSTOM_PATH}name": "'SnipSnap'",
            f"{bridge.SHELL_DCONF_ROOT}/show-screenshot-ui": (
                "['Print', '<Super>Print']"
            ),
            f"{bridge.SHELL_DCONF_ROOT}/screenshot": None,
            f"{bridge.SHELL_DCONF_ROOT}/screenshot-window": None,
            bridge.BRIDGE_DCONF_PATH: None,
            bridge.ANNOTATOR_DCONF_PATH: None,
        }
        self.commands: list[tuple[str, ...]] = []
        self.autostart_path: Path | None = None
        self.extension_known = False
        self.extension_enabled = False
        self.extension_active = False
        self.legacy_extension_known = False
        self.legacy_extension_enabled = False
        self.legacy_disable_clears_enabled = True
        self.enable_succeeds = True
        self.enable_becomes_active = True
        self.enable_hook: Callable[[], None] | None = None
        self.disable_clears_enabled = True
        self.disable_clears_active = True
        self.extension_state_override: int | None = None
        self.disable_result_state: int | None = None
        self.shell_version = "GNOME Shell 50.1"
        self.shell_owner = ":1.50"
        self.session = "Class=user\nType=wayland\nActive=yes\nLockedHint=no\n"

    @staticmethod
    def _gsettings_operation(command: tuple[str, ...]) -> tuple[int, str]:
        if len(command) > 2 and command[1] == "--schemadir":
            return 3, command[2]
        return 1, ""

    def _extension_shell_state(self) -> int:
        if self.extension_active:
            return bridge.EXTENSION_STATE_ACTIVE
        if self.extension_state_override is not None:
            return self.extension_state_override
        if self.extension_known:
            return bridge.EXTENSION_STATE_INITIALIZED
        return bridge.EXTENSION_STATE_UNINSTALLED

    def run(self, argv: Sequence[str], *, check: bool = True) -> bridge.CommandResult:
        command = tuple(str(argument) for argument in argv)
        self.commands.append(command)
        result = self._dispatch(command)
        if check and result.returncode != 0:
            raise bridge.BridgeError(
                f"fake command failed: {' '.join(command)}: {result.stderr}"
            )
        return result

    def _dispatch(self, command: tuple[str, ...]) -> bridge.CommandResult:
        if command == ("gnome-shell", "--version"):
            return bridge.CommandResult(command, 0, self.shell_version + "\n")
        if (
            command[:2] == ("gdbus", "call")
            and "org.freedesktop.DBus.GetNameOwner" in command
        ):
            return bridge.CommandResult(command, 0, f"('{self.shell_owner}',)\n")
        if (
            command[:2] == ("gdbus", "call")
            and "org.gnome.Shell.Extensions.GetExtensionInfo" in command
        ):
            state = self._extension_shell_state()
            if state == bridge.EXTENSION_STATE_UNINSTALLED:
                return bridge.CommandResult(command, 0, "(@a{sv} {},)\n")
            output = (
                "({'uuid': <'"
                + bridge.UUID
                + "'>, 'state': <"
                + f"{state}.0"
                + ">},)\n"
            )
            return bridge.CommandResult(command, 0, output)
        if command[:2] == ("loginctl", "show-session"):
            return bridge.CommandResult(command, 0, self.session)

        if command[0] == "gsettings":
            operation_index, _schema_dir = self._gsettings_operation(command)
            operation = command[operation_index]
            if operation == "list-keys":
                schema = command[operation_index + 1]
                keys = sorted(key for owner, key in self.values if owner == schema)
                return bridge.CommandResult(command, 0, "\n".join(keys) + "\n")
            schema = command[operation_index + 1]
            key = command[operation_index + 2]
            setting = (schema, key)
            if operation == "get":
                if setting not in self.values:
                    return bridge.CommandResult(command, 1, stderr="unknown setting")
                return bridge.CommandResult(command, 0, self.values[setting] + "\n")
            if operation == "set":
                value = command[operation_index + 3]
                self.values[setting] = value
                for path, mapped_setting in self.path_to_setting.items():
                    if mapped_setting == setting:
                        self.overrides[path] = value
                        break
                return bridge.CommandResult(command, 0)

        if command[:2] == ("dconf", "read"):
            value = self.overrides.get(command[2])
            return bridge.CommandResult(command, 0, (value + "\n") if value else "")
        if command[:2] == ("dconf", "write"):
            path, value = command[2], command[3]
            self.overrides[path] = value
            self.values[self.path_to_setting[path]] = value
            return bridge.CommandResult(command, 0)
        if command[:2] == ("dconf", "reset"):
            path = command[2]
            self.overrides[path] = None
            self.values[self.path_to_setting[path]] = self.defaults[path]
            return bridge.CommandResult(command, 0)

        if command == (
            "gnome-extensions",
            "list",
            "--enabled",
            "--active",
        ):
            output = (
                f"{bridge.UUID}\n"
                if self.extension_enabled and self.extension_active
                else ""
            )
            return bridge.CommandResult(command, 0, output)
        if command == ("gnome-extensions", "list", "--active"):
            output = f"{bridge.UUID}\n" if self.extension_active else ""
            return bridge.CommandResult(command, 0, output)
        if command == ("gnome-extensions", "list", "--enabled"):
            enabled = []
            if self.extension_enabled:
                enabled.append(bridge.UUID)
            if self.legacy_extension_enabled:
                enabled.append(bridge.LEGACY_UUID)
            output = "\n".join(enabled) + ("\n" if enabled else "")
            return bridge.CommandResult(command, 0, output)
        if command == ("gnome-extensions", "list"):
            known = []
            if self.extension_known:
                known.append(bridge.UUID)
            if self.legacy_extension_known:
                known.append(bridge.LEGACY_UUID)
            output = "\n".join(known) + ("\n" if known else "")
            return bridge.CommandResult(command, 0, output)
        if command[:2] == ("gnome-extensions", "enable"):
            if command[2] == bridge.LEGACY_UUID:
                self.legacy_extension_known = True
                self.legacy_extension_enabled = True
                return bridge.CommandResult(command, 0)
            if self.enable_succeeds:
                self.extension_known = True
                self.extension_enabled = True
                self.extension_active = self.enable_becomes_active
                self.extension_state_override = (
                    bridge.EXTENSION_STATE_ACTIVE
                    if self.extension_active
                    else bridge.EXTENSION_STATE_ERROR
                )
                if self.enable_hook is not None:
                    self.enable_hook()
            return bridge.CommandResult(command, 0)
        if command[:2] == ("gnome-extensions", "disable"):
            if command[2] == bridge.LEGACY_UUID:
                if self.legacy_disable_clears_enabled:
                    self.legacy_extension_enabled = False
                return bridge.CommandResult(command, 0)
            if self.disable_clears_enabled:
                self.extension_enabled = False
            if self.disable_clears_active:
                self.extension_active = False
            if self.disable_result_state is not None:
                self.extension_state_override = self.disable_result_state
            elif not self.extension_active:
                self.extension_state_override = bridge.EXTENSION_STATE_INACTIVE
            return bridge.CommandResult(command, 0)
        if command[:2] == ("gnome-extensions", "install"):
            self.extension_known = True
            return bridge.CommandResult(command, 0)

        if command[0] == "glib-compile-schemas":
            schema_dir = Path(command[-1])
            (schema_dir / "gschemas.compiled").write_bytes(b"fake-schema-cache")
            return bridge.CommandResult(command, 0)

        if command[:3] == ("snipsnap", "config", "--autostart"):
            assert self.autostart_path is not None, "autostart_path not wired"
            if command[3] == "true":
                self.autostart_path.parent.mkdir(parents=True, exist_ok=True)
                self.autostart_path.write_text(
                    "[Desktop Entry]\n"
                    "Name=SnipSnap\n"
                    "TryExec=snipsnap\n"
                    "Exec=snipsnap\n"
                    "Type=Application\n"
                    "X-GNOME-Autostart-enabled=true\n",
                    encoding="utf-8",
                )
            else:
                self.autostart_path.unlink(missing_ok=True)
            return bridge.CommandResult(command, 0)

        if len(command) >= 4 and command[0] == sys.executable:
            Path(command[-1]).write_bytes(b"fake-extension-bundle")
            return bridge.CommandResult(command, 0)

        return bridge.CommandResult(command, 127, stderr="unsupported fake command")

    @staticmethod
    def is_mutating(command: tuple[str, ...]) -> bool:
        if command[:2] in {
            ("gnome-extensions", "enable"),
            ("gnome-extensions", "disable"),
            ("gnome-extensions", "install"),
            ("dconf", "write"),
            ("dconf", "reset"),
            ("snipsnap", "config"),
        }:
            return True
        if command and command[0] == "glib-compile-schemas":
            return True
        if command and command[0] == "gsettings":
            operation_index, _ = FakeCommandExecutor._gsettings_operation(command)
            return command[operation_index] == "set"
        if command and command[0] == sys.executable:
            return True
        return False


class DefaultPathsTest(unittest.TestCase):
    def test_debian_installed_manager_uses_packaged_support_files(self) -> None:
        paths = bridge.paths_for_script(
            Path("/usr/bin/snipsnap-shell-bridge"),
            {
                "HOME": "/home/tester",
                "XDG_DATA_HOME": "/home/tester/.local/share",
                "XDG_STATE_HOME": "/home/tester/.local/state",
            },
        )

        support = Path("/usr/lib/snipsnap/gnome-shell")
        self.assertEqual(
            paths.source,
            support / "source" / bridge.SOURCE_DIRECTORY,
        )
        self.assertEqual(paths.packager, support / "package-extension.py")
        self.assertEqual(paths.install_manifest, support / "install-manifest.json")
        self.assertEqual(paths.validator, support / "validate_gnome_shell_extension.py")
        self.assertEqual(
            paths.installed,
            Path("/home/tester/.local/share/gnome-shell/extensions") / bridge.UUID,
        )


class ShellBridgeManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = root / "source" / bridge.SOURCE_DIRECTORY
        self.data_home = root / "data"
        self.installed = self.data_home / "gnome-shell" / "extensions" / bridge.UUID
        self.packager = root / "package-extension.py"
        self.packager.write_text("# fake packager\n", encoding="utf-8")
        self.validator = root / "validate_gnome_shell_extension.py"
        self.validator.write_text("# fake validator\n", encoding="utf-8")
        metadata = {
            "uuid": bridge.UUID,
            "shell-version": bridge.SUPPORTED_SHELL_VERSION_DECLARATIONS,
            "session-modes": ["user"],
            "settings-schema": bridge.BRIDGE_SCHEMA,
        }
        for extension_dir in (self.source, self.installed):
            (extension_dir / "schemas").mkdir(parents=True)
            (extension_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (extension_dir / "schemas" / "bridge.gschema.xml").write_text(
                "<schemalist/>\n", encoding="utf-8"
            )
            (extension_dir / "extension.js").write_text(
                "export default class Bridge {}\n", encoding="utf-8"
            )
        self.install_manifest = self.source.parent / "install-manifest.json"
        self.install_manifest.write_text(
            json.dumps(
                {
                    "format-version": 1,
                    "uuid": bridge.UUID,
                    "source-directory": bridge.SOURCE_DIRECTORY,
                    "files": [
                        "extension.js",
                        "metadata.json",
                        "schemas/bridge.gschema.xml",
                    ],
                    "generated-files": [bridge.GENERATED_SCHEMA_PATH],
                }
            ),
            encoding="utf-8",
        )
        (self.installed / bridge.GENERATED_SCHEMA_PATH).write_bytes(
            b"preexisting-fake-schema-cache"
        )

        self.executor = FakeCommandExecutor()
        self.store = MemoryStateStore()
        self.environ = {
            "HOME": str(root / "home"),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "GNOME",
            "XDG_SESSION_ID": "7",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "WAYLAND_DISPLAY": "wayland-0",
        }
        self.executor.autostart_path = (
            Path(self.environ["HOME"]) / ".config" / "autostart" / "SnipSnap.desktop"
        )
        self.legacy_autostart_path = (
            Path(self.environ["HOME"])
            / ".config"
            / "autostart"
            / bridge._LEGACY_AUTOSTART_ENTRY_NAME
        )
        self.manager = bridge.ShellBridgeManager(
            self.executor,
            self.store,
            environ=self.environ,
            effective_uid=1000,
            source_dir=self.source,
            installed_dir=self.installed,
            packager=self.packager,
            install_manifest=self.install_manifest,
            validator=self.validator,
            now=lambda: datetime(2026, 7, 18, tzinfo=timezone.utc),
            expected_file_uid=os.geteuid(),
            receiver_probe=lambda: None,
            annotator_probe=lambda: False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutating_commands(self) -> list[tuple[str, ...]]:
        return [
            command
            for command in self.executor.commands
            if self.executor.is_mutating(command)
        ]

    def prepare_and_restart(self, *, accept_installed: bool) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, "restart GNOME Shell"):
            self.manager.activate(accept_installed=accept_installed)
        self.assertEqual(self.store.state["phase"], "restart_required")
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )
        self.executor.shell_owner = ":1.51"

    def activate_after_restart(self, *, accept_installed: bool) -> dict[str, Any]:
        self.prepare_and_restart(accept_installed=accept_installed)
        return self.manager.activate(accept_installed=accept_installed)

    @staticmethod
    def is_print_owner_mutation(command: tuple[str, ...]) -> bool:
        if not command or command[0] != "gsettings":
            return False
        operation_index, _ = FakeCommandExecutor._gsettings_operation(command)
        if command[operation_index] != "set":
            return False
        schema = command[operation_index + 1]
        value = command[operation_index + 3]
        return (
            schema == bridge.SHELL_SCHEMA
            or schema
            == bridge.GSettingsBackend.schema_reference(
                bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
            )
            or (
                schema == bridge.BRIDGE_SCHEMA
                and bridge.parse_variant(value) == ["Print"]
            )
        )

    def test_install_enable_verify_then_move_print_is_idempotent(self) -> None:
        result = self.activate_after_restart(accept_installed=False)

        self.assertTrue(result["changed"])
        self.assertEqual(self.store.state["phase"], "activated")
        self.assertTrue(self.executor.extension_enabled)
        self.assertTrue(self.executor.extension_active)
        self.assertIn((bridge.SHELL_SCHEMA, "show-screenshot-ui"), self.executor.values)
        self.assertEqual(
            self.executor.values[(bridge.SHELL_SCHEMA, "show-screenshot-ui")],
            "['<Super>Print']",
        )
        custom_ref = bridge.GSettingsBackend.schema_reference(
            bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
        )
        self.assertEqual(self.executor.values[(custom_ref, "binding")], "''")
        self.assertEqual(
            self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)],
            "['Print']",
        )

        commands = self.executor.commands
        install_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ("gnome-extensions", "install")
        )
        compile_index = next(
            index
            for index, command in enumerate(commands)
            if command[0] == "glib-compile-schemas"
        )
        enable_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ("gnome-extensions", "enable")
        )
        verification_index = next(
            index
            for index in range(enable_index + 1, len(commands))
            if commands[index][:2] == ("gdbus", "call")
            and ("org.gnome.Shell.Extensions.GetExtensionInfo" in commands[index])
        )
        first_print_owner_mutation = next(
            index
            for index, command in enumerate(commands)
            if self.is_print_owner_mutation(command)
        )
        self.assertLess(install_index, compile_index)
        self.assertLess(compile_index, enable_index)
        self.assertLess(enable_index, verification_index)
        self.assertLess(verification_index, first_print_owner_mutation)

        mutations_before = list(self.mutating_commands())
        second = self.manager.activate(accept_installed=False)
        self.assertFalse(second["changed"])
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_clean_profile_without_custom_binding_can_activate_and_rollback(
        self,
    ) -> None:
        registry = (bridge.CUSTOM_REGISTRY_SCHEMA, bridge.CUSTOM_REGISTRY_KEY)
        custom_ref = bridge.GSettingsBackend.schema_reference(
            bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
        )
        self.executor.values[registry] = "[]"
        self.executor.values[(custom_ref, "binding")] = "''"
        self.executor.values[(custom_ref, "command")] = "''"
        self.executor.values[(custom_ref, "name")] = "''"
        for key in ("binding", "command", "name"):
            self.executor.overrides[f"{bridge.CUSTOM_PATH}{key}"] = None

        self.activate_after_restart(accept_installed=True)

        self.assertFalse(self.store.state["custom_registered_before"])
        self.assertEqual(self.executor.values[registry], "[]")
        self.assertEqual(
            self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)],
            "['Print']",
        )

        self.manager.rollback()
        self.assertEqual(self.executor.values[registry], "[]")
        self.assertEqual(self.executor.values[(custom_ref, "binding")], "''")

    def test_accept_installed_compiles_without_reinstall(self) -> None:
        self.activate_after_restart(accept_installed=True)

        self.assertTrue(
            any(
                command[0] == "glib-compile-schemas"
                for command in self.executor.commands
            )
        )
        self.assertFalse(
            any(
                command[:2] == ("gnome-extensions", "install")
                for command in self.executor.commands
            )
        )
        self.assertFalse(
            any(command[0] == sys.executable for command in self.executor.commands)
        )

    def test_stale_bridge_print_override_is_staged_safe_before_enable(self) -> None:
        self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)] = "['Print']"
        self.executor.overrides[bridge.BRIDGE_DCONF_PATH] = "['Print']"

        self.activate_after_restart(accept_installed=True)

        commands = self.executor.commands
        enable_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ("gnome-extensions", "enable")
        )
        bridge_writes = [
            (index, command)
            for index, command in enumerate(commands)
            if command[0] == "gsettings"
            and command[FakeCommandExecutor._gsettings_operation(command)[0]] == "set"
            and command[FakeCommandExecutor._gsettings_operation(command)[0] + 1]
            == bridge.BRIDGE_SCHEMA
        ]
        self.assertEqual(
            bridge.parse_variant(bridge_writes[0][1][-1]),
            ["<Super><Shift>s"],
        )
        self.assertLess(bridge_writes[0][0], enable_index)
        self.assertEqual(bridge.parse_variant(bridge_writes[-1][1][-1]), ["Print"])
        verification_index = next(
            index
            for index in range(enable_index + 1, len(commands))
            if commands[index][:2] == ("gdbus", "call")
            and ("org.gnome.Shell.Extensions.GetExtensionInfo" in commands[index])
        )
        self.assertLess(verification_index, bridge_writes[-1][0])

    def test_failed_enable_never_moves_print(self) -> None:
        self.executor.enable_succeeds = False
        self.prepare_and_restart(accept_installed=True)

        with self.assertRaisesRegex(
            bridge.BridgeError, "did not reach the enabled state"
        ):
            self.manager.activate(accept_installed=True)

        self.assertFalse(
            any(
                command[0] == "gsettings"
                and command[FakeCommandExecutor._gsettings_operation(command)[0]]
                == "set"
                for command in self.executor.commands
            )
        )
        custom_ref = bridge.GSettingsBackend.schema_reference(
            bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
        )
        self.assertEqual(self.executor.values[(custom_ref, "binding")], "'Print'")
        self.assertEqual(
            self.executor.values[(bridge.SHELL_SCHEMA, "show-screenshot-ui")],
            "['Print', '<Super>Print']",
        )
        self.assertIsNotNone(self.store.state)

    def test_unavailable_receiver_never_moves_print(self) -> None:
        self.manager.receiver_probe = lambda: (_ for _ in ()).throw(
            bridge.BridgeError("receiver unavailable")
        )
        self.prepare_and_restart(accept_installed=True)

        with self.assertRaisesRegex(bridge.BridgeError, "receiver unavailable"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "extension_verified")
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

    def test_activation_enables_receiver_autostart_and_rollback_removes_it(
        self,
    ) -> None:
        autostart = self.executor.autostart_path
        assert autostart is not None
        self.assertFalse(autostart.exists())

        self.activate_after_restart(accept_installed=True)

        self.assertTrue(autostart.is_file())
        self.assertIs(self.store.state["autostart_entry_before"], False)

        self.manager.rollback()
        self.assertFalse(autostart.exists())

    def test_activation_preserves_preexisting_autostart_entry(self) -> None:
        autostart = self.executor.autostart_path
        assert autostart is not None
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(
            "[Desktop Entry]\n"
            "TryExec=snipsnap\n"
            "Exec=snipsnap\n"
            "Type=Application\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )

        self.activate_after_restart(accept_installed=True)
        self.assertIs(self.store.state["autostart_entry_before"], True)

        self.manager.rollback()
        self.assertTrue(autostart.is_file())

    def test_activation_repairs_preexisting_invalid_autostart_entry(self) -> None:
        autostart = self.executor.autostart_path
        assert autostart is not None
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(
            "[Desktop Entry]\nType=Application\nExec=wrong-program\n",
            encoding="utf-8",
        )

        self.activate_after_restart(accept_installed=True)
        self.assertTrue(self.manager._autostart_entry_is_valid())
        self.assertIs(self.store.state["autostart_entry_before"], True)

    def test_rerun_with_dead_receiver_never_unbinds_print(self) -> None:
        # Regression: 2026-07-22 reference-desktop incident recovery. Rerunning
        # activate while the daemon is down must fail BEFORE staging moves the
        # bridge key off Print, otherwise Print ends up bound to nothing at all.
        self.activate_after_restart(accept_installed=True)
        self.assertEqual(
            bridge.parse_variant(
                self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)]
            ),
            ["Print"],
        )

        mutations_before = len(self.mutating_commands())
        self.manager.receiver_probe = lambda: (_ for _ in ()).throw(
            bridge.BridgeError("receiver unavailable")
        )
        with self.assertRaisesRegex(bridge.BridgeError, "receiver unavailable"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(len(self.mutating_commands()), mutations_before)
        self.assertEqual(
            bridge.parse_variant(
                self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)]
            ),
            ["Print"],
        )
        self.assertEqual(self.store.state["phase"], "activated")

    def test_activation_enables_external_annotator_when_satty_present(
        self,
    ) -> None:
        self.manager.annotator_probe = lambda: True
        ini = Path(self.environ["HOME"]) / ".config" / "snipsnap" / "snipsnap.ini"

        self.activate_after_restart(accept_installed=True)

        self.assertEqual(
            self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.ANNOTATOR_KEY)],
            "true",
        )
        self.assertIn(
            f"{bridge.ANNOTATOR_INI_KEY}=true", ini.read_text(encoding="utf-8")
        )
        annotator = self.store.state["annotator"]
        self.assertIsNone(annotator["override"])
        self.assertIsNone(annotator["ini_value"])

        self.manager.rollback()
        self.assertEqual(
            self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.ANNOTATOR_KEY)],
            "false",
        )
        self.assertNotIn(
            bridge.ANNOTATOR_INI_KEY, ini.read_text(encoding="utf-8")
        )

    def test_activation_without_satty_leaves_annotator_disabled(self) -> None:
        self.activate_after_restart(accept_installed=True)
        self.assertEqual(
            self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.ANNOTATOR_KEY)],
            "false",
        )
        ini = Path(self.environ["HOME"]) / ".config" / "snipsnap" / "snipsnap.ini"
        self.assertFalse(ini.exists())

    def test_annotator_ini_edit_preserves_existing_config(self) -> None:
        ini = Path(self.environ["HOME"]) / ".config" / "snipsnap" / "snipsnap.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text(
            "[General]\nsavePath=/home/user/Desktop\nshowHelp=false\n"
            "[Shortcuts]\nTYPE_SAVE=Ctrl+S\n",
            encoding="utf-8",
        )
        self.manager.annotator_probe = lambda: True

        self.activate_after_restart(accept_installed=True)
        lines = ini.read_text(encoding="utf-8").splitlines()
        # The key must land inside the [General] group, not top-level and not
        # inside [Shortcuts], or QSettings never reads it.
        self.assertEqual(
            lines[:2], ["[General]", f"{bridge.ANNOTATOR_INI_KEY}=true"]
        )
        self.assertIn("savePath=/home/user/Desktop", lines)
        self.assertIn("showHelp=false", lines)
        self.assertIn("TYPE_SAVE=Ctrl+S", lines)

        self.manager.rollback()
        content = ini.read_text(encoding="utf-8")
        self.assertIn("savePath=/home/user/Desktop", content)
        self.assertIn("TYPE_SAVE=Ctrl+S", content)
        self.assertNotIn(bridge.ANNOTATOR_INI_KEY, content)

    def test_activated_state_missing_autostart_field_heals_on_rerun(self) -> None:
        self.activate_after_restart(accept_installed=True)
        autostart = self.executor.autostart_path
        assert autostart is not None

        # Simulate a snapshot from before autostart management existed and an
        # entry lost since (the exact 2026-07-22 field state).
        del self.store.state["autostart_entry_before"]
        autostart.unlink()

        result = self.manager.activate(accept_installed=True)
        self.assertTrue(result["changed"])
        self.assertTrue(autostart.is_file())

        # Rollback with the field absent must leave the entry untouched.
        self.manager.rollback()
        self.assertTrue(autostart.is_file())

    def test_print_rebind_cycles_the_extension_after_the_key_moves(self) -> None:
        """Moving the bridge key to Print must be followed by a disable/enable.

        Activation enables the extension while the key is still on the safe
        binding, so Print is never taken from its previous owner until the
        extension is proven ACTIVE. That makes the move to Print a live
        reassignment, which on GNOME Shell 50 does not produce a working grab --
        measured on the reference desktop 2026-07-27, Print produced no trigger
        event at all until the extension was cycled. The extension's own
        re-grab reports a valid action id regardless, so only this ordering
        assertion catches a regression.
        """
        self.activate_after_restart(accept_installed=True)

        commands = [tuple(command) for command in self.executor.commands]

        def first(description, predicate, after=-1):
            for index, command in enumerate(commands):
                if index > after and predicate(command):
                    return index
            self.fail(
                f"no {description} after index {after}; the Print rebind is not "
                f"followed by an extension cycle, so the grab will not take. "
                f"commands after that point: {commands[after + 1:]}"
            )

        rebind = first(
            "bridge key set to Print",
            lambda c: c[0] == "gsettings"
            and bridge.BRIDGE_KEY in c
            and "['Print']" in c,
        )
        disable = first(
            "gnome-extensions disable",
            lambda c: c[:2] == ("gnome-extensions", "disable"),
            after=rebind,
        )
        first(
            "gnome-extensions enable",
            lambda c: c[:2] == ("gnome-extensions", "enable"),
            after=disable,
        )

    def test_digest_bound_enable_pending_requires_fresh_shell_to_resume(self) -> None:
        self.prepare_and_restart(accept_installed=True)
        self.executor.enable_succeeds = False

        with self.assertRaisesRegex(
            bridge.BridgeError, "did not reach the enabled state"
        ):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "enable_pending")
        pending = copy.deepcopy(self.store.state["enable_pending_payload"])
        self.assertEqual(pending["shell_epoch"]["dbus_owner"], ":1.51")
        compile_count = sum(
            command[0] == "glib-compile-schemas" for command in self.executor.commands
        )

        self.executor.enable_succeeds = True
        with self.assertRaisesRegex(bridge.BridgeError, "enable attempt is pending"):
            self.manager.activate(accept_installed=True)
        self.executor.shell_owner = ":1.52"
        result = self.manager.activate(accept_installed=True)

        self.assertTrue(result["changed"])
        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(
            self.store.state["runtime_payload"]["sha256"], pending["sha256"]
        )
        self.assertEqual(
            sum(
                command[0] == "glib-compile-schemas"
                for command in self.executor.commands
            ),
            compile_count,
        )
        self.assertFalse(
            any(
                command[:2] == ("gnome-extensions", "install")
                for command in self.executor.commands
            )
        )

    def test_payload_mutation_during_enable_never_moves_print(self) -> None:
        self.prepare_and_restart(accept_installed=True)

        def mutate_installed_payload() -> None:
            (self.installed / "extension.js").write_text(
                "export default class RacedPayload {}\n", encoding="utf-8"
            )

        self.executor.enable_hook = mutate_installed_payload
        with self.assertRaisesRegex(bridge.BridgeError, "differs from repository"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "runtime_tainted")
        self.assertIsNone(self.store.state["runtime_payload"])
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

        (self.installed / "extension.js").write_bytes(
            (self.source / "extension.js").read_bytes()
        )
        self.executor.enable_hook = None
        with self.assertRaisesRegex(bridge.BridgeError, "tainted extension runtime"):
            self.manager.activate(accept_installed=True)
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

        self.executor.shell_owner = ":1.52"
        result = self.manager.activate(accept_installed=True)
        self.assertTrue(result["changed"])
        self.assertEqual(self.store.state["phase"], "activated")

    def test_shell_owner_change_during_enable_never_moves_print(self) -> None:
        self.prepare_and_restart(accept_installed=True)

        def replace_shell_process() -> None:
            self.executor.shell_owner = ":1.99"

        self.executor.enable_hook = replace_shell_process
        with self.assertRaisesRegex(bridge.BridgeError, "process changed"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "runtime_tainted")
        self.assertEqual(self.store.state["tainted_shell_epoch"]["dbus_owner"], ":1.99")
        self.assertIsNone(self.store.state["runtime_payload"])
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

    def test_enabled_but_inactive_error_never_moves_print(self) -> None:
        self.executor.enable_becomes_active = False
        self.prepare_and_restart(accept_installed=True)

        with self.assertRaisesRegex(bridge.BridgeError, "ACTIVE state"):
            self.manager.activate(accept_installed=True)

        self.assertTrue(self.executor.extension_enabled)
        self.assertFalse(self.executor.extension_active)
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )
        custom_ref = bridge.GSettingsBackend.schema_reference(
            bridge.CUSTOM_SCHEMA, bridge.CUSTOM_PATH
        )
        self.assertEqual(self.executor.values[(custom_ref, "binding")], "'Print'")

    def test_untrusted_preloaded_runtime_is_rejected_before_install(self) -> None:
        self.executor.extension_known = True
        self.executor.extension_enabled = True
        self.executor.extension_active = True
        original_values = copy.deepcopy(self.executor.values)
        original_overrides = copy.deepcopy(self.executor.overrides)

        with self.assertRaisesRegex(bridge.BridgeError, "untrusted extension"):
            self.manager.activate(accept_installed=False)

        self.assertEqual(self.store.state["phase"], "snapshotted")
        self.assertIsNone(self.store.state["prepared_payload"])
        self.assertIsNone(self.store.state["enable_pending_payload"])
        self.assertIsNone(self.store.state["runtime_payload"])
        self.assertEqual(self.executor.values, original_values)
        self.assertEqual(self.executor.overrides, original_overrides)
        self.assertEqual(self.mutating_commands(), [])

    def test_rollback_restores_exact_snapshot_and_is_idempotent(self) -> None:
        original_overrides = copy.deepcopy(self.executor.overrides)
        self.activate_after_restart(accept_installed=True)

        result = self.manager.rollback()

        self.assertTrue(result["changed"])
        self.assertFalse(self.executor.extension_enabled)
        self.assertEqual(self.executor.overrides, original_overrides)
        self.assertEqual(self.store.state["phase"], "rolled_back")

        mutations_before = list(self.mutating_commands())
        repeated = self.manager.rollback()
        self.assertFalse(repeated["changed"])
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_legacy_bridge_retires_at_handover_and_rollback_restores_it(
        self,
    ) -> None:
        self.executor.legacy_extension_known = True
        self.executor.legacy_extension_enabled = True

        self.prepare_and_restart(accept_installed=True)

        legacy_disable = (
            "gnome-extensions",
            "disable",
            bridge.LEGACY_UUID,
        )
        self.assertTrue(self.executor.legacy_extension_enabled)
        self.assertTrue(self.store.state["legacy_extension_enabled_before"])
        self.assertNotIn(legacy_disable, self.executor.commands)

        result = self.manager.activate(accept_installed=True)

        self.assertTrue(result["changed"])
        self.assertFalse(self.executor.legacy_extension_enabled)
        commands = self.executor.commands
        enable_index = commands.index(
            ("gnome-extensions", "enable", bridge.UUID)
        )
        legacy_disable_index = commands.index(legacy_disable)
        self.assertLess(enable_index, legacy_disable_index)
        self.assertTrue(
            any(
                "org.gnome.Shell.Extensions.GetExtensionInfo" in command
                for command in commands[enable_index + 1 : legacy_disable_index]
            ),
            "replacement runtime must be ACTIVE before legacy retirement",
        )
        print_mutation_indices = [
            index
            for index, command in enumerate(commands)
            if self.is_print_owner_mutation(command)
        ]
        self.assertTrue(print_mutation_indices)
        self.assertLess(legacy_disable_index, min(print_mutation_indices))

        self.manager.rollback()

        self.assertTrue(self.executor.legacy_extension_enabled)
        self.assertIn(
            ("gnome-extensions", "enable", bridge.LEGACY_UUID),
            self.executor.commands,
        )
        mutations_before = list(self.mutating_commands())
        repeated = self.manager.rollback()
        self.assertFalse(repeated["changed"])
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_legacy_autostart_retires_at_handover_and_restores_exactly(
        self,
    ) -> None:
        original_contents = (
            b"[Desktop Entry]\nType=Application\n"
            b"Exec=/usr/bin/legacy-screenshot\nX-Test=\xff\n"
        )
        original_mode = 0o664
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        # The reference desktop's Ubuntu session uses umask 0002, so its real
        # user-owned ~/.config/autostart is 0775. Pin that Linux layout on
        # every platform instead of relying on the host running this test to
        # share its umask.
        self.legacy_autostart_path.parent.chmod(0o775)
        self.legacy_autostart_path.write_bytes(original_contents)
        self.legacy_autostart_path.chmod(original_mode)

        self.prepare_and_restart(accept_installed=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        legacy_snapshot = self.store.state["legacy_autostart_entry_before"]
        self.assertTrue(legacy_snapshot["exists"])
        self.assertEqual(legacy_snapshot["mode"], original_mode)

        receiver_proven = False

        def prove_receiver() -> None:
            nonlocal receiver_proven
            receiver_proven = True

        original_retire = self.manager._retire_legacy_autostart_entry

        def retire_after_replacement_proof(state: Mapping[str, Any]) -> bool:
            self.assertTrue(self.executor.extension_active)
            self.assertTrue(receiver_proven)
            self.assertTrue(self.executor.autostart_path.is_file())
            return original_retire(state)

        self.manager.receiver_probe = prove_receiver
        with mock.patch.object(
            self.manager,
            "_retire_legacy_autostart_entry",
            side_effect=retire_after_replacement_proof,
        ):
            result = self.manager.activate(accept_installed=True)

        self.assertTrue(result["changed"])
        self.assertFalse(self.legacy_autostart_path.exists())
        self.assertTrue(self.executor.autostart_path.is_file())

        self.manager.rollback()

        self.assertEqual(self.legacy_autostart_path.read_bytes(), original_contents)
        self.assertEqual(
            stat.S_IMODE(self.legacy_autostart_path.stat().st_mode), original_mode
        )
        mutations_before = list(self.mutating_commands())
        repeated = self.manager.rollback()
        self.assertFalse(repeated["changed"])
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_group_writable_legacy_autostart_requires_private_config_root(
        self,
    ) -> None:
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o755)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o664)

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "group-writable legacy autostart entry is not confined",
        ):
            self.manager.activate(accept_installed=True, dry_run=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_group_writable_legacy_autostart_rejects_hardlink(self) -> None:
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.parent.chmod(0o775)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o664)
        os.link(
            self.legacy_autostart_path,
            self.legacy_autostart_path.with_name("linked.desktop"),
        )

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "legacy pre-rename autostart entry has unsafe hard links",
        ):
            self.manager.activate(accept_installed=True, dry_run=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_group_writable_legacy_autostart_rejects_wrong_owner(self) -> None:
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.parent.chmod(0o775)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o664)
        wrong_owner_manager = bridge.ShellBridgeManager(
            self.executor,
            self.store,
            environ=self.environ,
            effective_uid=1000,
            source_dir=self.source,
            installed_dir=self.installed,
            packager=self.packager,
            install_manifest=self.install_manifest,
            validator=self.validator,
            expected_file_uid=os.geteuid() + 1,
            receiver_probe=lambda: None,
            annotator_probe=lambda: False,
        )

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "legacy pre-rename autostart entry is not owned by this user",
        ):
            wrong_owner_manager.activate(accept_installed=True, dry_run=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_group_writable_legacy_autostart_rejects_symlinked_config_root(
        self,
    ) -> None:
        home = Path(self.environ["HOME"])
        home.mkdir(parents=True)
        real_config = home.parent / "real-config"
        real_autostart = real_config / "autostart"
        real_autostart.mkdir(parents=True)
        real_config.chmod(0o700)
        real_autostart.chmod(0o775)
        (home / ".config").symlink_to(real_config, target_is_directory=True)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o664)

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "group-writable legacy autostart entry is not confined",
        ):
            self.manager.activate(accept_installed=True, dry_run=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_group_writable_legacy_restore_rechecks_private_config_root(
        self,
    ) -> None:
        original_contents = b"[Desktop Entry]\nExec=legacy-screenshot\n"
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.parent.chmod(0o775)
        self.legacy_autostart_path.write_bytes(original_contents)
        self.legacy_autostart_path.chmod(0o664)
        self.activate_after_restart(accept_installed=True)
        self.assertFalse(self.legacy_autostart_path.exists())

        config_home.chmod(0o755)
        with self.assertRaisesRegex(
            bridge.BridgeError,
            "cannot restore a group-writable legacy autostart entry",
        ):
            self.manager.rollback()
        self.assertFalse(self.legacy_autostart_path.exists())

        config_home.chmod(0o700)
        self.manager.rollback()
        self.assertEqual(self.legacy_autostart_path.read_bytes(), original_contents)
        self.assertEqual(
            stat.S_IMODE(self.legacy_autostart_path.stat().st_mode), 0o664
        )

    def test_world_writable_legacy_autostart_is_always_rejected(self) -> None:
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o666)

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "legacy pre-rename autostart entry is writable by other users",
        ):
            self.manager.activate(accept_installed=True, dry_run=True)

        self.assertTrue(self.legacy_autostart_path.is_file())
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_clean_install_never_creates_legacy_autostart_entry(self) -> None:
        self.assertFalse(self.legacy_autostart_path.exists())

        self.activate_after_restart(accept_installed=True)

        self.assertEqual(
            self.store.state["legacy_autostart_entry_before"], {"exists": False}
        )
        self.assertFalse(self.legacy_autostart_path.exists())

        self.manager.rollback()
        self.assertFalse(self.legacy_autostart_path.exists())

    def test_world_writable_legacy_autostart_restore_directory_is_rejected(
        self,
    ) -> None:
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.write_bytes(
            b"[Desktop Entry]\nExec=legacy-screenshot\n"
        )
        self.legacy_autostart_path.chmod(0o600)
        self.activate_after_restart(accept_installed=True)
        self.assertFalse(self.legacy_autostart_path.exists())
        self.legacy_autostart_path.parent.chmod(0o777)

        with self.assertRaisesRegex(
            bridge.BridgeError, "legacy autostart restore directory is unsafe"
        ):
            self.manager.rollback()

        self.assertFalse(self.legacy_autostart_path.exists())

    def test_unsafe_legacy_autostart_symlink_is_rejected_before_mutation(
        self,
    ) -> None:
        target = Path(self.temporary.name) / "do-not-touch.desktop"
        original_contents = b"[Desktop Entry]\nExec=do-not-touch\n"
        target.write_bytes(original_contents)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.symlink_to(target)

        with self.assertRaisesRegex(
            bridge.BridgeError,
            "cannot safely inspect the legacy pre-rename autostart entry",
        ):
            self.manager.activate(accept_installed=True)

        self.assertTrue(self.legacy_autostart_path.is_symlink())
        self.assertEqual(target.read_bytes(), original_contents)
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

    def test_changed_legacy_autostart_is_never_deleted_or_given_print(
        self,
    ) -> None:
        original_contents = b"[Desktop Entry]\nExec=legacy-before\n"
        changed_contents = b"[Desktop Entry]\nExec=changed-after-snapshot\n"
        config_home = self.legacy_autostart_path.parents[1]
        config_home.mkdir(parents=True)
        config_home.chmod(0o700)
        self.legacy_autostart_path.parent.mkdir(parents=True)
        self.legacy_autostart_path.parent.chmod(0o775)
        self.legacy_autostart_path.write_bytes(original_contents)
        self.legacy_autostart_path.chmod(0o664)
        self.prepare_and_restart(accept_installed=True)
        self.legacy_autostart_path.write_bytes(changed_contents)

        with self.assertRaisesRegex(
            bridge.BridgeError, "changed after the snapshot"
        ):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.legacy_autostart_path.read_bytes(), changed_contents)
        self.assertEqual(self.store.state["phase"], "extension_verified")
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

    def test_activation_upgrades_snapshot_missing_legacy_state(self) -> None:
        self.activate_after_restart(accept_installed=True)
        assert self.store.state is not None
        del self.store.state["legacy_extension_enabled_before"]
        self.executor.legacy_extension_known = True
        self.executor.legacy_extension_enabled = True

        result = self.manager.activate(accept_installed=True)

        self.assertTrue(result["changed"])
        self.assertTrue(self.store.state["legacy_extension_enabled_before"])
        self.assertFalse(self.executor.legacy_extension_enabled)

        self.manager.rollback()
        self.assertTrue(self.executor.legacy_extension_enabled)

    def test_failed_legacy_retirement_never_moves_print(self) -> None:
        self.executor.legacy_extension_known = True
        self.executor.legacy_extension_enabled = True
        self.prepare_and_restart(accept_installed=True)
        self.executor.legacy_disable_clears_enabled = False

        with self.assertRaisesRegex(
            bridge.BridgeError, "legacy pre-rename shell bridge is still enabled"
        ):
            self.manager.activate(accept_installed=True)

        self.assertTrue(self.executor.legacy_extension_enabled)
        self.assertEqual(self.store.state["phase"], "extension_verified")
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

        self.manager.rollback()
        self.assertTrue(self.executor.legacy_extension_enabled)

    def test_activation_removes_gsd_registry_entry_and_rollback_restores_it(
        self,
    ) -> None:
        registry = (bridge.CUSTOM_REGISTRY_SCHEMA, bridge.CUSTOM_REGISTRY_KEY)
        self.assertIn(bridge.CUSTOM_PATH, self.executor.values[registry])

        self.activate_after_restart(accept_installed=True)

        # Blanking the custom keybinding's binding does not release mutter's
        # stale gsd ShellKeyGrabber grab on Wayland; the entry must leave the
        # registry list so gsd drops the grab and stops shadowing the bridge.
        self.assertNotIn(bridge.CUSTOM_PATH, self.executor.values[registry])

        self.manager.rollback()
        self.assertIn(bridge.CUSTOM_PATH, self.executor.values[registry])

    def test_rollback_refuses_restore_while_extension_remains_active(self) -> None:
        self.activate_after_restart(accept_installed=True)
        self.executor.extension_enabled = False
        self.executor.extension_active = True
        self.executor.disable_clears_active = False
        active_overrides = copy.deepcopy(self.executor.overrides)

        with self.assertRaisesRegex(bridge.BridgeError, "found ACTIVE"):
            self.manager.rollback()

        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(self.executor.overrides, active_overrides)
        self.assertFalse(self.executor.extension_enabled)
        self.assertTrue(self.executor.extension_active)

    def test_rollback_refuses_restore_when_disable_enters_error(self) -> None:
        self.activate_after_restart(accept_installed=True)
        self.executor.disable_result_state = bridge.EXTENSION_STATE_ERROR
        active_overrides = copy.deepcopy(self.executor.overrides)

        with self.assertRaisesRegex(bridge.BridgeError, "found ERROR"):
            self.manager.rollback()

        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(self.executor.overrides, active_overrides)
        self.assertFalse(self.executor.extension_enabled)
        self.assertFalse(self.executor.extension_active)
        self.assertEqual(
            self.executor._extension_shell_state(), bridge.EXTENSION_STATE_ERROR
        )

    def test_repeated_rollback_repairs_reenabled_extension_and_setting_drift(
        self,
    ) -> None:
        self.activate_after_restart(accept_installed=True)
        self.manager.rollback()
        self.executor.extension_enabled = True
        self.executor.extension_active = True
        self.executor.overrides[bridge.BRIDGE_DCONF_PATH] = "['Print']"
        self.executor.values[(bridge.BRIDGE_SCHEMA, bridge.BRIDGE_KEY)] = "['Print']"

        result = self.manager.rollback()

        self.assertTrue(result["changed"])
        self.assertFalse(self.executor.extension_enabled)
        self.assertFalse(self.executor.extension_active)
        self.assertIsNone(self.executor.overrides[bridge.BRIDGE_DCONF_PATH])

    def test_rolled_back_matching_payload_reuses_trusted_runtime(self) -> None:
        self.activate_after_restart(accept_installed=True)
        self.manager.rollback()
        compile_count = sum(
            command[0] == "glib-compile-schemas" for command in self.executor.commands
        )

        result = self.manager.activate(accept_installed=True)

        self.assertTrue(result["changed"])
        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(
            sum(
                command[0] == "glib-compile-schemas"
                for command in self.executor.commands
            ),
            compile_count,
        )

    def test_activated_payload_change_requires_rollback_before_upgrade(self) -> None:
        self.activate_after_restart(accept_installed=True)
        replacement = "export default class Replacement {}\n"
        (self.source / "extension.js").write_text(replacement, encoding="utf-8")
        (self.installed / "extension.js").write_text(replacement, encoding="utf-8")
        mutations_before = list(self.mutating_commands())

        with self.assertRaisesRegex(bridge.BridgeError, "roll back before"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_dry_run_and_status_are_read_only(self) -> None:
        activation = self.manager.activate(accept_installed=True, dry_run=True)
        status = self.manager.status()

        self.assertTrue(activation["dry_run"])
        self.assertEqual(status["action"], "status")
        self.assertFalse(status["extension_enabled"])
        self.assertFalse(status["extension_active"])
        self.assertEqual(status["extension_state"], "UNINSTALLED")
        self.assertEqual(status["receiver"], {"ready": True, "error": None})
        self.assertIsNone(self.store.state)
        self.assertEqual(self.mutating_commands(), [])

        self.manager.receiver_probe = lambda: (_ for _ in ()).throw(
            bridge.BridgeError("receiver unavailable")
        )
        unavailable = self.manager.status()
        self.assertEqual(
            unavailable["receiver"],
            {"ready": False, "error": "receiver unavailable"},
        )
        self.assertEqual(self.mutating_commands(), [])

    def test_installed_payload_must_match_manifest_bytes(self) -> None:
        (self.installed / "extension.js").write_text(
            "export default class Mutated {}\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(bridge.BridgeError, "differs from repository"):
            self.manager.activate(accept_installed=True)

        self.assertFalse(self.executor.extension_enabled)
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

    def test_installed_tree_rejects_extra_file_and_payload_symlink(self) -> None:
        (self.installed / "evil.js").write_text("malicious\n", encoding="utf-8")
        with self.assertRaisesRegex(bridge.BridgeError, "extra_files"):
            self.manager.activate(accept_installed=True)

        (self.installed / "evil.js").unlink()
        (self.installed / "extension.js").unlink()
        (self.installed / "extension.js").symlink_to(self.source / "extension.js")
        with self.assertRaisesRegex(bridge.BridgeError, "contains a symlink"):
            self.manager.activate(accept_installed=True)

        self.assertFalse(self.executor.extension_enabled)

    def test_source_install_gets_the_same_post_install_tree_check(self) -> None:
        (self.installed / "left-behind.js").write_text("undeclared\n", encoding="utf-8")

        with self.assertRaisesRegex(bridge.BridgeError, "extra_files"):
            self.manager.activate(accept_installed=False)

        package_command = next(
            command
            for command in self.executor.commands
            if command and command[0] == sys.executable
        )
        self.assertEqual(
            package_command,
            (
                sys.executable,
                str(self.manager.packager),
                "--validator",
                str(self.manager.validator),
                "--manifest",
                str(self.manager.install_manifest),
                str(self.manager.source_dir),
                package_command[-1],
            ),
        )

        self.assertTrue(
            any(
                command[:2] == ("gnome-extensions", "install")
                for command in self.executor.commands
            )
        )
        self.assertFalse(self.executor.extension_enabled)
        self.assertFalse(
            any(
                self.is_print_owner_mutation(command)
                for command in self.executor.commands
            )
        )

    def test_installed_extension_root_symlink_is_rejected_lexically(self) -> None:
        real_extension = Path(self.temporary.name) / "real-installed" / bridge.UUID
        real_extension.parent.mkdir(parents=True)
        shutil.copytree(self.installed, real_extension)
        shutil.rmtree(self.installed)
        self.installed.symlink_to(real_extension, target_is_directory=True)

        with self.assertRaisesRegex(bridge.BridgeError, "must not be a symlink"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.manager.installed_dir, self.installed)
        self.assertFalse(self.executor.extension_enabled)

    def test_rollback_dry_run_leaves_active_state_untouched(self) -> None:
        self.activate_after_restart(accept_installed=True)
        mutations_before = list(self.mutating_commands())

        result = self.manager.rollback(dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertTrue(self.executor.extension_enabled)
        self.assertEqual(self.store.state["phase"], "activated")
        self.assertEqual(self.mutating_commands(), mutations_before)

    def test_preflight_accepts_shell_51_beta(self) -> None:
        self.executor.shell_version = "GNOME Shell 51.beta"

        self.prepare_and_restart(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "restart_required")

    def test_preflight_accepts_shell_51_stable_version_string(self) -> None:
        self.executor.shell_version = "GNOME Shell 51.0"

        self.prepare_and_restart(accept_installed=True)

        self.assertEqual(self.store.state["phase"], "restart_required")

    def test_preflight_rejects_unsupported_shell_without_shortcut_mutation(self) -> None:
        self.executor.shell_version = "GNOME Shell 49.4"
        self.executor.legacy_extension_known = True
        self.executor.legacy_extension_enabled = True

        with self.assertRaisesRegex(bridge.BridgeError, "major 50 or 51"):
            self.manager.activate(accept_installed=True)

        self.assertEqual(self.mutating_commands(), [])
        self.assertEqual(self.store.state["phase"], "snapshotted")
        self.assertTrue(self.store.state["legacy_extension_enabled_before"])
        self.assertTrue(self.executor.legacy_extension_enabled)

    def test_activation_rejects_root_and_non_user_extension_path(self) -> None:
        root_manager = bridge.ShellBridgeManager(
            self.executor,
            self.store,
            environ=self.environ,
            effective_uid=0,
            source_dir=self.source,
            installed_dir=self.installed,
            packager=self.packager,
        )
        with self.assertRaisesRegex(bridge.BridgeError, "as root"):
            root_manager.activate(accept_installed=True)

        wrong_path_manager = bridge.ShellBridgeManager(
            self.executor,
            self.store,
            environ=self.environ,
            effective_uid=1000,
            source_dir=self.source,
            installed_dir=Path(self.temporary.name) / "system-extension" / bridge.UUID,
            packager=self.packager,
        )
        with self.assertRaisesRegex(bridge.BridgeError, "XDG data"):
            wrong_path_manager.activate(accept_installed=True)

        self.assertEqual(self.mutating_commands(), [])

    def test_installed_tree_rejects_files_not_owned_by_expected_user(self) -> None:
        wrong_owner_manager = bridge.ShellBridgeManager(
            self.executor,
            self.store,
            environ=self.environ,
            effective_uid=1000,
            source_dir=self.source,
            installed_dir=self.installed,
            packager=self.packager,
            expected_file_uid=os.geteuid() + 1,
        )

        with self.assertRaisesRegex(bridge.BridgeError, "not owned by this user"):
            wrong_owner_manager.activate(accept_installed=True)

        self.assertFalse(self.executor.extension_enabled)


class ReceiverProbeExecutor:
    def __init__(self, output: str) -> None:
        self.output = output
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, check: bool = True) -> bridge.CommandResult:
        del check
        command = tuple(str(argument) for argument in argv)
        self.commands.append(command)
        return bridge.CommandResult(command, 0, self.output)


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED"),
    "Linux SO_PEERCRED is required",
)
class ReceiverProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.receiver_dir = self.runtime / "snipsnap"
        self.receiver = self.receiver_dir / "gnome-shell-bridge-v1.sock"
        self.runtime.mkdir(mode=0o700)
        self.receiver_dir.mkdir(mode=0o700)
        self.receiver_process: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        self.stop_receiver()
        self.temporary.cleanup()

    def bind_receiver(self, *, mode: int = 0o600) -> None:
        script = """
import os
import socket
import sys

listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(sys.argv[1])
os.chmod(sys.argv[1], int(sys.argv[2], 8))
listener.listen(8)
print("READY", flush=True)
sys.stdin.buffer.read()
listener.close()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.receiver), oct(mode)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        if process.stdout.readline().strip() != "READY":
            stderr = process.stderr.read() if process.stderr is not None else ""
            process.wait(timeout=2)
            self.fail(f"receiver process failed to start: {stderr}")
        self.receiver_process = process

    def stop_receiver(self) -> None:
        process = self.receiver_process
        self.receiver_process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    @property
    def receiver_pid(self) -> int:
        assert self.receiver_process is not None
        return self.receiver_process.pid

    def manager(
        self,
        output: str | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        effective_uid: int | None = None,
    ) -> tuple[bridge.ShellBridgeManager, ReceiverProbeExecutor]:
        if output is None:
            peer_pid = (
                self.receiver_pid
                if self.receiver_process is not None
                else max(os.getpid(), 2)
            )
            output = f"(uint32 {peer_pid},)\n"
        executor = ReceiverProbeExecutor(output)
        manager = bridge.ShellBridgeManager(
            executor,
            MemoryStateStore(),
            environ=(
                {"XDG_RUNTIME_DIR": str(self.runtime)} if environ is None else environ
            ),
            effective_uid=(os.geteuid() if effective_uid is None else effective_uid),
            source_dir=self.root / "source",
            installed_dir=self.root / "installed",
            packager=self.root / "package-extension.py",
        )
        return manager, executor

    def test_secure_receiver_and_matching_dbus_owner_succeed(self) -> None:
        self.bind_receiver()
        manager, executor = self.manager()

        manager.receiver_probe()

        self.assertEqual(
            executor.commands,
            [
                (
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.DBus",
                    "--object-path",
                    "/org/freedesktop/DBus",
                    "--method",
                    "org.freedesktop.DBus.GetConnectionUnixProcessID",
                    "tech.norvi.snipsnap",
                )
            ],
        )

    def test_missing_runtime_directory_environment_is_rejected(self) -> None:
        manager, executor = self.manager(environ={})

        with self.assertRaisesRegex(bridge.BridgeError, "XDG_RUNTIME_DIR"):
            manager.receiver_probe()

        self.assertEqual(executor.commands, [])

    def test_non_owner_only_paths_and_non_exact_socket_mode_are_rejected(
        self,
    ) -> None:
        self.bind_receiver()
        manager, executor = self.manager()

        cases = (
            (self.runtime, 0o755, "not owner-only"),
            (self.receiver_dir, 0o750, "not owner-only"),
            (self.receiver, 0o640, "not owner-only"),
            (self.receiver, 0o400, "exactly 0600"),
        )
        for path, bad_mode, message in cases:
            with self.subTest(path=path, mode=oct(bad_mode)):
                original_mode = stat.S_IMODE(path.lstat().st_mode)
                os.chmod(path, bad_mode)
                try:
                    with self.assertRaisesRegex(bridge.BridgeError, message):
                        manager.receiver_probe()
                finally:
                    os.chmod(path, original_mode)

        self.assertEqual(executor.commands, [])

    def test_wrong_path_owner_is_rejected(self) -> None:
        self.bind_receiver()
        manager, executor = self.manager(effective_uid=os.geteuid() + 1)

        with self.assertRaisesRegex(bridge.BridgeError, "wrong owner"):
            manager.receiver_probe()

        self.assertEqual(executor.commands, [])

    def test_socket_and_parent_symlinks_are_rejected(self) -> None:
        self.bind_receiver()
        manager, executor = self.manager()
        real_socket = self.receiver_dir / "real.sock"
        self.receiver.rename(real_socket)
        self.receiver.symlink_to(real_socket.name)

        with self.assertRaisesRegex(bridge.BridgeError, "unsafe.*path type"):
            manager.receiver_probe()

        self.assertEqual(executor.commands, [])

        self.receiver.unlink()
        self.stop_receiver()
        real_socket.unlink()
        self.receiver_dir.rmdir()
        real_receiver_dir = self.root / "real-snipsnap"
        real_receiver_dir.mkdir(mode=0o700)
        self.receiver_dir.symlink_to(real_receiver_dir, target_is_directory=True)

        with self.assertRaisesRegex(bridge.BridgeError, "unsafe.*path type"):
            manager.receiver_probe()

        self.assertEqual(executor.commands, [])

    def test_stale_socket_inode_is_rejected(self) -> None:
        self.bind_receiver()
        self.stop_receiver()
        manager, executor = self.manager()

        with self.assertRaisesRegex(
            bridge.BridgeError, "cannot authenticate bridge receiver"
        ):
            manager.receiver_probe()

        self.assertEqual(executor.commands, [])

    def test_malformed_or_mismatched_dbus_owner_is_rejected(self) -> None:
        self.bind_receiver()
        outputs = (
            "not a gdbus result\n",
            f"(uint32 {self.receiver_pid + 1},)\n",
        )

        for output in outputs:
            with self.subTest(output=output.strip()):
                manager, executor = self.manager(output)
                with self.assertRaisesRegex(
                    bridge.BridgeError,
                    "socket peer is not the active SnipSnap D-Bus owner",
                ):
                    manager.receiver_probe()
                self.assertEqual(len(executor.commands), 1)

    def test_invalid_peer_credentials_are_rejected(self) -> None:
        self.bind_receiver()
        manager, executor = self.manager()
        invalid_credentials = (
            struct.pack("3i", 1, os.geteuid(), os.getegid()),
            struct.pack("3i", self.receiver_pid, os.geteuid() + 1, os.getegid()),
        )

        for credentials in invalid_credentials:
            with self.subTest(credentials=credentials):
                connection = mock.MagicMock()
                connection.__enter__.return_value = connection
                connection.getsockopt.return_value = credentials
                with mock.patch.object(
                    bridge.socket, "socket", return_value=connection
                ):
                    with self.assertRaisesRegex(
                        bridge.BridgeError, "peer credentials are invalid"
                    ):
                        manager.receiver_probe()

        self.assertEqual(executor.commands, [])


class FileStateStoreTest(unittest.TestCase):
    def test_state_is_atomic_owner_only_and_rejects_broad_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private-state" / "activation.json"
            store = bridge.FileStateStore(path, owner_uid=os.geteuid())
            store.save({"format_version": 1, "phase": "snapshotted"})

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(store.load()["phase"], "snapshotted")

            path.chmod(0o644)
            with self.assertRaisesRegex(bridge.BridgeError, "owner-only"):
                store.load()

    def test_state_file_final_symlink_is_not_resolved_before_lstat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private-state"
            directory.mkdir(mode=0o700)
            target = directory / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            target.chmod(0o600)
            state_path = directory / "activation.json"
            state_path.symlink_to(target)
            store = bridge.FileStateStore(state_path, owner_uid=os.geteuid())

            self.assertEqual(store.path, state_path)
            with self.assertRaisesRegex(bridge.BridgeError, "unsafe state path type"):
                store.load()

    def test_state_directory_final_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_directory = root / "real-state"
            real_directory.mkdir(mode=0o700)
            linked_directory = root / "linked-state"
            linked_directory.symlink_to(real_directory, target_is_directory=True)
            store = bridge.FileStateStore(
                linked_directory / "activation.json", owner_uid=os.geteuid()
            )

            with self.assertRaisesRegex(bridge.BridgeError, "unsafe state path type"):
                store.load()
            with self.assertRaisesRegex(bridge.BridgeError, "unsafe state path type"):
                store.save({"phase": "snapshotted"})


if __name__ == "__main__":
    unittest.main()
