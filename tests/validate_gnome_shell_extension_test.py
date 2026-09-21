#!/usr/bin/env python3
"""Mutation tests for the GNOME Shell extension security validator."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "tests" / "validate_gnome_shell_extension.py"
PACKAGER = REPO_ROOT / "packaging" / "gnome-shell" / "package-extension.py"
SOURCE_PARENT = REPO_ROOT / "contrib" / "gnome-shell-extension"
SOURCE_DIRECTORY = "snipsnap-shell-bridge"


class ExtensionValidatorSecurityTest(unittest.TestCase):
    def run_validator(self, extension_root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(extension_root)],
            check=False,
            capture_output=True,
            text=True,
        )

    def stage_extension(self, temporary_root: Path) -> Path:
        staged_parent = temporary_root / "gnome-shell-extension"
        shutil.copytree(SOURCE_PARENT, staged_parent)
        return staged_parent / SOURCE_DIRECTORY

    def test_unmodified_extension_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_validator(self.stage_extension(Path(temporary)))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_metadata_cannot_claim_an_unaudited_shell_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            metadata_path = extension_root / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["shell-version"].append("52")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("audited GNOME Shell 50 and 51", result.stderr)

    def test_packager_normalizes_runner_checkout_write_bits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            extension_root = self.stage_extension(temporary_root)
            for path in extension_root.rglob("*"):
                if path.is_file():
                    path.chmod(0o666)

            bundle = temporary_root / "extension.zip"
            result = subprocess.run(
                [sys.executable, str(PACKAGER), str(extension_root), str(bundle)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            with zipfile.ZipFile(bundle) as archive:
                member_modes = {
                    info.filename: (info.external_attr >> 16) & 0o777
                    for info in archive.infolist()
                }

        self.assertTrue(member_modes)
        self.assertEqual(set(member_modes.values()), {0o644})

    def test_packager_accepts_explicit_installed_support_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            support = temporary_root / "usr" / "lib" / "snipsnap" / "gnome-shell"
            extension_root = support / "source" / SOURCE_DIRECTORY
            shutil.copytree(SOURCE_PARENT / SOURCE_DIRECTORY, extension_root)
            manifest = support / "install-manifest.json"
            shutil.copy2(SOURCE_PARENT / "install-manifest.json", manifest)
            bundle = temporary_root / "extension.zip"

            result = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGER),
                    "--validator",
                    str(VALIDATOR),
                    "--manifest",
                    str(manifest),
                    str(extension_root),
                    str(bundle),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(bundle.is_file())

    def test_forbidden_operations_in_secondary_module_fail(self) -> None:
        mutations = {
            "subprocess": ("\nGio.Subprocess.new([], 0);\n", "subprocess execution"),
            "filesystem": (
                "\nGio.File.new_for_path('/tmp/capture');\n",
                "filesystem access",
            ),
            "dbus": ("\nGio.DBusProxy.new_for_bus_sync();\n", "D-Bus API"),
            "network": (
                "\nGio.NetworkAddress.new('example.invalid', 443);\n",
                "network socket address",
            ),
        }

        for name, (source, expected_error) in mutations.items():
            with self.subTest(
                operation=name
            ), tempfile.TemporaryDirectory() as temporary:
                extension_root = self.stage_extension(Path(temporary))
                geometry = extension_root / "geometry.js"
                geometry.write_text(
                    geometry.read_text(encoding="utf-8") + source,
                    encoding="utf-8",
                )
                result = self.run_validator(extension_root)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(
                    f"geometry.js contains forbidden {expected_error}",
                    result.stderr,
                )

    def test_socket_client_is_allowed_only_in_pinned_handoff_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            geometry = extension_root / "geometry.js"
            geometry.write_text(
                geometry.read_text(encoding="utf-8") + "\nnew Gio.SocketClient();\n",
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("may not use sensitive API Gio.SocketClient", result.stderr)

    def test_handoff_client_cannot_enable_proxy_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            client = extension_root / "handoff-client.js"
            client.write_text(
                client.read_text(encoding="utf-8").replace(
                    "client.set_enable_proxy(false)",
                    "client.set_enable_proxy(true)",
                ),
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("missing 'client.set_enable_proxy(false)'", result.stderr)

    def test_handoff_client_cannot_add_another_dbus_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            client = extension_root / "handoff-client.js"
            client.write_text(
                client.read_text(encoding="utf-8")
                + "\nGio.DBus.session.call();\n",
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("exactly the two approved D-Bus calls", result.stderr)

    def test_handoff_client_dbus_method_names_must_be_call_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            client = extension_root / "handoff-client.js"
            source = client.read_text(encoding="utf-8")
            for method in ("GetConnectionUnixProcessID", "StartServiceByName"):
                self.assertIn(f"'{method}',", source)
                source = source.replace(
                    f"'{method}',", f"'ListNames', // {method}", 1
                )
            client.write_text(source, encoding="utf-8")
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(
            "GetConnectionUnixProcessID and StartServiceByName", result.stderr
        )

    def test_extension_cannot_add_synchronous_staged_editor_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            extension = extension_root / "extension.js"
            extension.write_text(
                extension.read_text(encoding="utf-8")
                + "\nMain.activateWindow(editorWindow);\n",
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("only in its guarded compositor callback", result.stderr)

    def test_extension_cannot_relocate_editor_raise_outside_deferred_method(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            extension = extension_root / "extension.js"
            source = extension.read_text(encoding="utf-8")
            source = source.replace(
                "                Main.activateWindow(editorWindow);\n",
                "",
                1,
            )
            extension.write_text(
                source + "\nMain.activateWindow(editorWindow);\n",
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("only in its guarded compositor callback", result.stderr)

    def test_extension_cannot_remove_bounded_focus_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            extension = extension_root / "extension.js"
            extension.write_text(
                extension.read_text(encoding="utf-8").replace(
                    "return GLib.SOURCE_CONTINUE;",
                    "return GLib.SOURCE_REMOVE;",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("GLib.SOURCE_CONTINUE", result.stderr)

    def test_session_transition_must_cancel_deferred_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            extension = extension_root / "extension.js"
            extension.write_text(
                extension.read_text(encoding="utf-8").replace(
                    "if (!this._isUserSession()) {\n"
                    "                this._cancelEditorActivation();\n"
                    "                this._cleanupOverlay('session-mode-changed');",
                    "if (!this._isUserSession()) {\n"
                    "                this._cleanupOverlay('session-mode-changed');",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("session transitions must cancel", result.stderr)

    def test_extension_uses_gnome_50_cancellable_connect_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extension_root = self.stage_extension(Path(temporary))
            extension = extension_root / "extension.js"
            extension.write_text(
                extension.read_text(encoding="utf-8").replace(
                    "cancellable.connect(() => {",
                    "cancellable.connect('cancelled', () => {",
                ),
                encoding="utf-8",
            )
            result = self.run_validator(extension_root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("callback-only connect API", result.stderr)


if __name__ == "__main__":
    unittest.main()
