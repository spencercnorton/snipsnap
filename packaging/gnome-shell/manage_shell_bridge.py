#!/usr/bin/env python3
"""Safely activate or roll back the GNOME Shell capture bridge.

This helper is intentionally user-scoped.  It never invokes sudo, package
managers, or Debian maintainer scripts. Existing Print owners are not changed
until the exact payload has crossed a fresh-Shell process barrier, been enabled,
and been independently verified ACTIVE.
"""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence


UUID = "snipsnap-shell-bridge@kleos.norvi.tech"
# The UUID this extension shipped under before the rename. GNOME keys the
# enabled list by UUID, so an upgrade leaves the old one enabled and pointing
# at a directory dpkg has already deleted -- which surfaces to the user as a
# broken extension rather than as a rename.
LEGACY_UUID = "flameshot-shell-bridge@kleos.norvi.tech"
# The pristine source checkout (repository and /usr/lib support copy) is not
# named by UUID; only the installed extension directory is.
SOURCE_DIRECTORY = "snipsnap-shell-bridge"
_LEGACY_AUTOSTART_ENTRY_NAME = "Flameshot.desktop"
_MAX_LEGACY_AUTOSTART_BYTES = 1024 * 1024
STATE_FORMAT_VERSION = 2
SUPPORTED_SHELL_MAJORS = frozenset({50, 51})
SUPPORTED_SHELL_VERSION_DECLARATIONS = ["50", "51"]

EXTENSION_STATE_ACTIVE = 1
EXTENSION_STATE_INACTIVE = 2
EXTENSION_STATE_ERROR = 3
EXTENSION_STATE_OUT_OF_DATE = 4
EXTENSION_STATE_DOWNLOADING = 5
EXTENSION_STATE_INITIALIZED = 6
EXTENSION_STATE_DEACTIVATING = 7
EXTENSION_STATE_ACTIVATING = 8
EXTENSION_STATE_UNINSTALLED = 99
EXTENSION_STATE_NAMES = {
    EXTENSION_STATE_ACTIVE: "ACTIVE",
    EXTENSION_STATE_INACTIVE: "INACTIVE",
    EXTENSION_STATE_ERROR: "ERROR",
    EXTENSION_STATE_OUT_OF_DATE: "OUT_OF_DATE",
    EXTENSION_STATE_DOWNLOADING: "DOWNLOADING",
    EXTENSION_STATE_INITIALIZED: "INITIALIZED",
    EXTENSION_STATE_DEACTIVATING: "DEACTIVATING",
    EXTENSION_STATE_ACTIVATING: "ACTIVATING",
    EXTENSION_STATE_UNINSTALLED: "UNINSTALLED",
}
SAFE_DISABLED_EXTENSION_STATES = {
    EXTENSION_STATE_INACTIVE,
    EXTENSION_STATE_INITIALIZED,
    EXTENSION_STATE_UNINSTALLED,
}

CUSTOM_REGISTRY_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_REGISTRY_KEY = "custom-keybindings"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/" "custom-keybindings/snipsnap/"
)
EXPECTED_CUSTOM_COMMAND = "snipsnap gui"

SHELL_SCHEMA = "org.gnome.shell.keybindings"
SHELL_DCONF_ROOT = "/org/gnome/shell/keybindings"
REQUIRED_SCREENSHOT_KEY = "show-screenshot-ui"

BRIDGE_SCHEMA = "org.gnome.shell.extensions.flameshot-shell-bridge"
BRIDGE_KEY = "show-capture-overlay"
BRIDGE_DCONF_PATH = (
    "/org/gnome/shell/extensions/flameshot-shell-bridge/" "show-capture-overlay"
)
ANNOTATOR_KEY = "external-annotator"
ANNOTATOR_DCONF_PATH = (
    "/org/gnome/shell/extensions/flameshot-shell-bridge/" "external-annotator"
)
ANNOTATOR_INI_KEY = "bridgeUseExternalAnnotator"
SAFE_BRIDGE_BINDING = "['<Super><Shift>s']"
GENERATED_SCHEMA_PATH = "schemas/gschemas.compiled"

MUTATING_PROGRAMS_NEVER_ALLOWED = {
    "apt",
    "apt-get",
    "dpkg",
    "pkexec",
    "su",
    "sudo",
}


class BridgeError(RuntimeError):
    """A safety or activation invariant was not satisfied."""


def lexical_absolute(path: Path) -> Path:
    """Return an absolute, normalized path without following symlinks."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandExecutor(Protocol):
    def run(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        """Run one argv-only command and return captured text output."""


class StateStore(Protocol):
    @property
    def location(self) -> str:
        """Human-readable state location."""

    def load(self) -> dict[str, Any] | None:
        """Load the activation snapshot, if one exists."""

    def save(self, state: Mapping[str, Any]) -> None:
        """Atomically persist the activation snapshot."""


class SubprocessCommandExecutor:
    """Production argv-only command executor."""

    def run(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        command = tuple(str(argument) for argument in argv)
        if not command:
            raise BridgeError("refusing to execute an empty command")
        program = Path(command[0]).name
        if program in MUTATING_PROGRAMS_NEVER_ALLOWED:
            raise BridgeError(f"forbidden privileged command: {program}")

        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        result = CommandResult(
            argv=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BridgeError(
                f"command failed ({result.returncode}): "
                f"{' '.join(command)}: {detail}"
            )
        return result


class FileStateStore:
    """Owner-only, atomic JSON snapshot storage."""

    def __init__(self, path: Path, *, owner_uid: int | None = None) -> None:
        self.path = lexical_absolute(path)
        self.owner_uid = os.geteuid() if owner_uid is None else owner_uid

    @property
    def location(self) -> str:
        return str(self.path)

    def _validate_owner_only(self, path: Path, *, directory: bool) -> None:
        metadata = path.lstat()
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if path.is_symlink() or not expected_type(metadata.st_mode):
            raise BridgeError(f"unsafe state path type: {path}")
        if metadata.st_uid != self.owner_uid:
            raise BridgeError(f"state path is not owned by this user: {path}")
        if metadata.st_mode & 0o077:
            raise BridgeError(f"state path is not owner-only: {path}")

    def load(self) -> dict[str, Any] | None:
        try:
            self.path.parent.lstat()
        except FileNotFoundError:
            return None
        self._validate_owner_only(self.path.parent, directory=True)
        try:
            self.path.lstat()
        except FileNotFoundError:
            return None
        self._validate_owner_only(self.path, directory=False)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BridgeError(f"cannot read activation state: {error}") from error
        if not isinstance(value, dict):
            raise BridgeError("activation state must contain a JSON object")
        return value

    def save(self, state: Mapping[str, Any]) -> None:
        directory = self.path.parent
        created = False
        try:
            directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=0o700, parents=True)
            created = True
        if created:
            os.chmod(directory, 0o700)
        self._validate_owner_only(directory, directory=True)
        try:
            self.path.lstat()
        except FileNotFoundError:
            pass
        else:
            self._validate_owner_only(self.path, directory=False)

        payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=directory
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)


class GSettingsBackend:
    def __init__(self, executor: CommandExecutor) -> None:
        self.executor = executor

    @staticmethod
    def schema_reference(schema: str, path: str | None = None) -> str:
        return f"{schema}:{path}" if path else schema

    @staticmethod
    def _base(schema_dir: Path | None = None) -> list[str]:
        command = ["gsettings"]
        if schema_dir is not None:
            command.extend(["--schemadir", str(schema_dir)])
        return command

    def get(
        self,
        schema: str,
        key: str,
        *,
        path: str | None = None,
        schema_dir: Path | None = None,
    ) -> str:
        command = self._base(schema_dir)
        command.extend(["get", self.schema_reference(schema, path), key])
        return self.executor.run(command).stdout.strip()

    def set(
        self,
        schema: str,
        key: str,
        value: str,
        *,
        path: str | None = None,
        schema_dir: Path | None = None,
    ) -> None:
        command = self._base(schema_dir)
        command.extend(["set", self.schema_reference(schema, path), key, value])
        self.executor.run(command)

    def list_keys(self, schema: str, *, path: str | None = None) -> list[str]:
        result = self.executor.run(
            ["gsettings", "list-keys", self.schema_reference(schema, path)]
        )
        return sorted(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )

    def read_override(self, dconf_path: str) -> str | None:
        result = self.executor.run(["dconf", "read", dconf_path], check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BridgeError(f"cannot read dconf key {dconf_path}: {detail}")
        value = result.stdout.strip()
        return value if value else None

    def restore_override(self, dconf_path: str, value: str | None) -> None:
        if value is None:
            self.executor.run(["dconf", "reset", dconf_path])
        else:
            self.executor.run(["dconf", "write", dconf_path, value])


def parse_variant(raw: str) -> Any:
    """Parse the string, boolean, and string-array GVariants this helper uses."""

    value = raw.strip()
    if value.startswith("@as "):
        value = value[4:].strip()
    if value in ("true", "false"):
        return value == "true"
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError) as error:
        raise BridgeError(f"unsupported GVariant value: {raw!r}") from error
    if isinstance(parsed, tuple):
        parsed = list(parsed)
    if not isinstance(parsed, (str, list)):
        raise BridgeError(f"unsupported GVariant type: {raw!r}")
    if isinstance(parsed, list) and not all(isinstance(item, str) for item in parsed):
        raise BridgeError(f"GVariant array must contain only strings: {raw!r}")
    return parsed


def format_variant(value: str | list[str]) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(repr(item) for item in value) + "]"
    return repr(value)


def remove_print_accelerator(raw: str) -> str:
    value = parse_variant(raw)
    if not isinstance(value, list):
        raise BridgeError(f"expected accelerator array, got: {raw!r}")
    return format_variant(
        [accelerator for accelerator in value if accelerator != "Print"]
    )


class ShellBridgeManager:
    def __init__(
        self,
        executor: CommandExecutor,
        state_store: StateStore,
        *,
        environ: Mapping[str, str],
        effective_uid: int,
        source_dir: Path,
        installed_dir: Path,
        packager: Path,
        install_manifest: Path | None = None,
        validator: Path | None = None,
        now: Callable[[], datetime] | None = None,
        expected_file_uid: int | None = None,
        receiver_probe: Callable[[], None] | None = None,
        annotator_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.executor = executor
        self.settings = GSettingsBackend(executor)
        self.state_store = state_store
        self.environ = dict(environ)
        self.effective_uid = effective_uid
        self.source_dir = source_dir.resolve()
        self.installed_dir = lexical_absolute(installed_dir)
        self.packager = packager.resolve()
        self.install_manifest = (
            install_manifest
            if install_manifest is not None
            else self.source_dir.parent / "install-manifest.json"
        ).resolve()
        self.validator = (
            validator
            if validator is not None
            else Path(__file__).resolve().parents[2]
            / "tests"
            / "validate_gnome_shell_extension.py"
        ).resolve()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.expected_file_uid = (
            effective_uid if expected_file_uid is None else expected_file_uid
        )
        self.receiver_probe = receiver_probe or self._probe_receiver
        # Both binaries: the spawn hardwires --copy-command wl-copy, so
        # enabling satty without wl-clipboard would break its copy action.
        self.annotator_probe = annotator_probe or (
            lambda: shutil.which("satty") is not None
            and shutil.which("wl-copy") is not None
        )

    @property
    def installed_schema_dir(self) -> Path:
        return self.installed_dir / "schemas"

    def _ensure_unprivileged(self) -> None:
        if self.effective_uid == 0:
            raise BridgeError("refusing to modify a desktop session as root")
        if self.environ.get("SUDO_UID") or self.environ.get("PKEXEC_UID"):
            raise BridgeError("refusing to run through sudo or pkexec")

        home = Path(self.environ.get("HOME", str(Path.home()))).expanduser()
        data_home = Path(
            self.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))
        ).expanduser()
        expected = lexical_absolute(data_home / "gnome-shell" / "extensions" / UUID)
        if self.installed_dir != expected:
            raise BridgeError(
                "installed extension path must be the current user's XDG data "
                f"directory: expected {expected}, found {self.installed_dir}"
            )
        try:
            metadata = self.installed_dir.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise BridgeError(
                f"installed extension path must not be a symlink: {self.installed_dir}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise BridgeError(
                f"installed extension path is not a directory: {self.installed_dir}"
            )

    def _probe_receiver(self) -> None:
        """Require the warm, owner-only receiver before Print can move."""

        runtime_value = self.environ.get("XDG_RUNTIME_DIR", "")
        if not runtime_value:
            raise BridgeError("XDG_RUNTIME_DIR is required for the bridge receiver")
        runtime = lexical_absolute(Path(runtime_value))
        receiver_dir = runtime / "snipsnap"
        receiver = receiver_dir / "gnome-shell-bridge-v1.sock"

        for path, expected_type in (
            (runtime, stat.S_ISDIR),
            (receiver_dir, stat.S_ISDIR),
            (receiver, stat.S_ISSOCK),
        ):
            try:
                metadata = path.lstat()
            except FileNotFoundError as error:
                raise BridgeError(f"bridge receiver is not ready: {path}") from error
            if path.is_symlink() or not expected_type(metadata.st_mode):
                raise BridgeError(f"unsafe bridge receiver path type: {path}")
            if metadata.st_uid != self.effective_uid:
                raise BridgeError(f"bridge receiver path has the wrong owner: {path}")
            if metadata.st_mode & 0o077:
                raise BridgeError(f"bridge receiver path is not owner-only: {path}")
        if receiver.lstat().st_mode & 0o777 != 0o600:
            raise BridgeError("bridge receiver socket mode must be exactly 0600")

        try:
            peer_format = "3i"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.25)
                connection.connect(str(receiver))
                credentials = connection.getsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_PEERCRED,
                    struct.calcsize(peer_format),
                )
                peer_pid, peer_uid, _peer_gid = struct.unpack(
                    peer_format, credentials
                )
        except (AttributeError, OSError, struct.error) as error:
            raise BridgeError(f"cannot authenticate bridge receiver: {error}") from error
        if peer_pid <= 1 or peer_uid != self.effective_uid:
            raise BridgeError("bridge receiver peer credentials are invalid")

        result = self.executor.run(
            [
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
            ]
        )
        match = re.fullmatch(r"\(uint32 ([1-9][0-9]*),?\)", result.stdout.strip())
        if match is None or int(match.group(1)) != peer_pid:
            raise BridgeError(
                "bridge socket peer is not the active SnipSnap D-Bus owner"
            )

    def _autostart_entry_path(self) -> Path:
        return self._config_home_path() / "autostart" / "SnipSnap.desktop"

    def _config_home_path(self) -> Path:
        home = Path(self.environ.get("HOME", str(Path.home()))).expanduser()
        return Path(
            self.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        ).expanduser()

    def _autostart_entry_exists(self) -> bool:
        return self._autostart_entry_path().is_file()

    def _autostart_entry_is_valid(self) -> bool:
        """Match the minimum contract written by ConfigHandler."""

        path = self._autostart_entry_path()
        try:
            data = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        return (
            data.startswith("[Desktop Entry]\n")
            and "\nType=Application\n" in data
            and "\nTryExec=snipsnap\n" in data
            and "\nExec=snipsnap\n" in data
            and "\nX-GNOME-Autostart-enabled=true\n" in data
        )

    def _legacy_autostart_entry_path(self) -> Path:
        return (
            self._config_home_path()
            / "autostart"
            / _LEGACY_AUTOSTART_ENTRY_NAME
        )

    def _legacy_group_write_is_confined(self) -> bool:
        """Allow an old umask-0002 file only behind a private config root.

        Older SnipSnap/Flameshot releases created their desktop entry with the
        session umask. Ubuntu installations commonly use 0002, producing a
        0664 file in a 0775 autostart directory. That file is still private
        when the user-owned XDG config root is 0700, as it is on the
        reference desktop: other group members cannot traverse the enclosing
        directory. Keep rejecting the same mode when that confinement is
        absent, and never follow a
        symlinked config root or autostart directory.
        """

        config_home = self._config_home_path()
        autostart_directory = self._legacy_autostart_entry_path().parent
        if not config_home.is_absolute():
            return False
        try:
            config_metadata = config_home.lstat()
            autostart_metadata = autostart_directory.lstat()
        except OSError:
            return False
        return (
            stat.S_ISDIR(config_metadata.st_mode)
            and not stat.S_ISLNK(config_metadata.st_mode)
            and config_metadata.st_uid == self.expected_file_uid
            and stat.S_IMODE(config_metadata.st_mode) & 0o077 == 0
            and stat.S_ISDIR(autostart_metadata.st_mode)
            and not stat.S_ISLNK(autostart_metadata.st_mode)
            and autostart_metadata.st_uid == self.expected_file_uid
            and not autostart_metadata.st_mode & stat.S_IWOTH
        )

    def _snapshot_legacy_autostart_entry(self) -> dict[str, Any]:
        """Capture a small, user-owned regular file without following it."""

        path = self._legacy_autostart_entry_path()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return {"exists": False}
        except OSError as error:
            raise BridgeError(
                "cannot safely inspect the legacy pre-rename autostart entry"
            ) from error

        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISREG(metadata.st_mode):
                raise BridgeError(
                    "legacy pre-rename autostart entry is not a regular file"
                )
            if metadata.st_uid != self.expected_file_uid:
                raise BridgeError(
                    "legacy pre-rename autostart entry is not owned by this user"
                )
            if metadata.st_nlink != 1:
                raise BridgeError(
                    "legacy pre-rename autostart entry has unsafe hard links"
                )
            if mode & stat.S_IWOTH:
                raise BridgeError(
                    "legacy pre-rename autostart entry is writable by other users"
                )
            if mode & stat.S_IWGRP and not self._legacy_group_write_is_confined():
                raise BridgeError(
                    "group-writable legacy autostart entry is not confined "
                    "by a private user config directory"
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                contents = stream.read(_MAX_LEGACY_AUTOSTART_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if len(contents) > _MAX_LEGACY_AUTOSTART_BYTES:
            raise BridgeError("legacy pre-rename autostart entry is unreasonably large")
        return {
            "exists": True,
            "contents_base64": base64.b64encode(contents).decode("ascii"),
            "mode": mode,
        }

    @staticmethod
    def _validate_legacy_autostart_snapshot(snapshot: Any) -> None:
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("exists"), bool
        ):
            raise TypeError
        if snapshot["exists"] is False:
            if set(snapshot) != {"exists"}:
                raise TypeError
            return
        if set(snapshot) != {"exists", "contents_base64", "mode"}:
            raise TypeError
        encoded = snapshot["contents_base64"]
        mode = snapshot["mode"]
        if not isinstance(encoded, str):
            raise TypeError
        if isinstance(mode, bool) or not isinstance(mode, int):
            raise TypeError
        if not 0 <= mode <= 0o777 or mode & stat.S_IWOTH:
            raise TypeError
        try:
            contents = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise TypeError from error
        if (
            len(contents) > _MAX_LEGACY_AUTOSTART_BYTES
            or base64.b64encode(contents).decode("ascii") != encoded
        ):
            raise TypeError

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(directory, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise BridgeError(
                "could not durably update the legacy autostart directory"
            ) from error

    def _retire_legacy_autostart_entry(self, state: Mapping[str, Any]) -> bool:
        """Remove only the exact legacy entry protected by the snapshot."""

        expected = state.get("legacy_autostart_entry_before")
        if expected is None:
            return False
        current = self._snapshot_legacy_autostart_entry()
        if expected["exists"] is False:
            if current["exists"]:
                raise BridgeError(
                    "a legacy pre-rename autostart entry appeared after the "
                    "snapshot; refusing to remove an unsnapshotted file"
                )
            return False
        if current["exists"] is False:
            return False
        if current != expected:
            raise BridgeError(
                "legacy pre-rename autostart entry changed after the snapshot; "
                "refusing to remove it"
            )
        try:
            self._legacy_autostart_entry_path().unlink()
            self._sync_directory(self._legacy_autostart_entry_path().parent)
        except OSError as error:
            raise BridgeError(
                "could not retire the legacy pre-rename autostart entry"
            ) from error
        if self._snapshot_legacy_autostart_entry()["exists"]:
            raise BridgeError(
                "legacy pre-rename autostart entry still exists after retirement"
            )
        return True

    def _restore_legacy_autostart_entry(self, state: Mapping[str, Any]) -> bool:
        """Atomically restore the snapshotted entry with exact bytes and mode."""

        expected = state.get("legacy_autostart_entry_before")
        if expected is None or expected["exists"] is False:
            return False
        current = self._snapshot_legacy_autostart_entry()
        if current == expected:
            return False
        if current["exists"]:
            raise BridgeError(
                "refusing to overwrite a changed legacy pre-rename autostart entry"
            )

        path = self._legacy_autostart_entry_path()
        directory = path.parent
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_metadata = directory.lstat()
        except OSError as error:
            raise BridgeError(
                "cannot create the legacy autostart restore directory"
            ) from error
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != self.expected_file_uid
            # Ubuntu commonly creates this user-owned directory as 0775 when
            # the session umask is 0002 (the reference desktop does). A
            # group-writable legacy file is accepted only behind the
            # separately verified private XDG config root; reject a
            # directory writable by arbitrary users.
            or directory_metadata.st_mode & stat.S_IWOTH
        ):
            raise BridgeError("legacy autostart restore directory is unsafe")
        if expected["mode"] & stat.S_IWGRP and not (
            self._legacy_group_write_is_confined()
        ):
            raise BridgeError(
                "cannot restore a group-writable legacy autostart entry "
                "outside a private user config directory"
            )

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".snipsnap-autostart-restore.", dir=directory
        )
        temporary_path = Path(temporary_name)
        try:
            contents = base64.b64decode(
                expected["contents_base64"], validate=True
            )
            os.fchmod(descriptor, expected["mode"])
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            if self._snapshot_legacy_autostart_entry()["exists"]:
                raise BridgeError(
                    "legacy pre-rename autostart entry appeared during rollback"
                )
            os.replace(temporary_path, path)
            self._sync_directory(directory)
        except (OSError, binascii.Error, ValueError) as error:
            raise BridgeError(
                "could not restore the legacy pre-rename autostart entry"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

        if self._snapshot_legacy_autostart_entry() != expected:
            raise BridgeError(
                "rollback verification failed for the legacy autostart entry"
            )
        return True

    def _ensure_receiver_autostart(self) -> bool:
        """Persist the receiver across logins; the bridge is dead without it.

        Activation proves the receiver is alive once, but the extension needs
        it at every capture. Reuse snipsnap's own autostart writer
        (ConfigHandler::setStartupLaunch) so the config UI, the CLI, and this
        manager agree on a single ~/.config/autostart/SnipSnap.desktop entry.
        """

        if self._autostart_entry_is_valid():
            return False
        self.executor.run(["snipsnap", "config", "--autostart", "true"])
        if not self._autostart_entry_is_valid():
            raise BridgeError(
                "snipsnap did not create a valid autostart entry "
                f"{self._autostart_entry_path()}; the receiver would not "
                "survive the next login"
            )
        return True

    def _snipsnap_ini_path(self) -> Path:
        home = Path(self.environ.get("HOME", str(Path.home()))).expanduser()
        config_home = Path(
            self.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        ).expanduser()
        return config_home / "snipsnap" / "snipsnap.ini"

    def _read_ini_annotator(self) -> str | None:
        try:
            lines = self._snipsnap_ini_path().read_text(
                encoding="utf-8"
            ).splitlines()
        except FileNotFoundError:
            return None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{ANNOTATOR_INI_KEY}="):
                return stripped.split("=", 1)[1]
        return None

    def _write_ini_annotator(self, value: str | None) -> None:
        """Targeted line edit; never reformats the rest of snipsnap.ini."""

        path = self._snipsnap_ini_path()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        lines = [
            line
            for line in lines
            if not line.strip().startswith(f"{ANNOTATOR_INI_KEY}=")
        ]
        if value is not None:
            entry = f"{ANNOTATOR_INI_KEY}={value}"
            for index, line in enumerate(lines):
                if line.strip() == "[General]":
                    lines.insert(index + 1, entry)
                    break
            else:
                lines.extend(["[General]", entry])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

    def _enable_external_annotator(self) -> bool:
        """Point committed captures at satty on both sides of the handshake.

        The extension gschema key goes first: gschema-on with config-off only
        degrades (early commit, internal editor still opens), while config-on
        with gschema-off would lose captures to the editor-window wait.
        """

        changed = False
        if not self._setting_is(
            BRIDGE_SCHEMA,
            ANNOTATOR_KEY,
            "true",
            schema_dir=self.installed_schema_dir,
        ):
            self.settings.set(
                BRIDGE_SCHEMA,
                ANNOTATOR_KEY,
                "true",
                schema_dir=self.installed_schema_dir,
            )
            changed = True
        if self._read_ini_annotator() != "true":
            self._write_ini_annotator("true")
            changed = True
        return changed

    def _snapshot_setting(
        self,
        schema: str,
        key: str,
        dconf_path: str,
        *,
        path: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": schema,
            "path": path,
            "key": key,
            "dconf_path": dconf_path,
            "effective": self.settings.get(schema, key, path=path),
            "override": self.settings.read_override(dconf_path),
        }

    def _capture_snapshot(self) -> dict[str, Any]:
        custom_keys = self.settings.list_keys(CUSTOM_SCHEMA, path=CUSTOM_PATH)
        required_custom_keys = {"binding", "command", "name"}
        missing_custom = required_custom_keys - set(custom_keys)
        if missing_custom:
            raise BridgeError(
                "custom SnipSnap binding is missing keys: "
                + ", ".join(sorted(missing_custom))
            )

        custom_values = {
            key: self._snapshot_setting(
                CUSTOM_SCHEMA,
                key,
                f"{CUSTOM_PATH}{key}",
                path=CUSTOM_PATH,
            )
            for key in sorted(required_custom_keys)
        }

        registry = self.settings.get(CUSTOM_REGISTRY_SCHEMA, CUSTOM_REGISTRY_KEY)
        registry_values = parse_variant(registry)
        if not isinstance(registry_values, list):
            raise BridgeError("the custom-keybindings registry is not an array")
        custom_registered_before = CUSTOM_PATH in registry_values
        if not custom_registered_before:
            # A clean GNOME install has no application-owned custom
            # shortcut. Snapshot schema defaults so rollback can remain exact;
            # activation will assign Print only after the bridge is ACTIVE.
            custom_values = {
                key: self._snapshot_setting(
                    CUSTOM_SCHEMA,
                    key,
                    f"{CUSTOM_PATH}{key}",
                    path=CUSTOM_PATH,
                )
                for key in sorted(required_custom_keys)
            }

        shell_keys = [
            key for key in self.settings.list_keys(SHELL_SCHEMA) if "screenshot" in key
        ]
        if REQUIRED_SCREENSHOT_KEY not in shell_keys:
            raise BridgeError(
                f"{SHELL_SCHEMA} does not expose {REQUIRED_SCREENSHOT_KEY}"
            )
        shell_values = {
            key: self._snapshot_setting(
                SHELL_SCHEMA,
                key,
                f"{SHELL_DCONF_ROOT}/{key}",
            )
            for key in shell_keys
        }

        return {
            "format_version": STATE_FORMAT_VERSION,
            "uuid": UUID,
            "phase": "snapshotted",
            "created_at": self.now().isoformat(),
            "prepared_payload": None,
            "enable_pending_payload": None,
            "runtime_payload": None,
            "tainted_shell_epoch": None,
            "custom_registry": registry,
            "custom_registered_before": custom_registered_before,
            "legacy_extension_enabled_before": self._is_extension_enabled(
                LEGACY_UUID
            ),
            "legacy_autostart_entry_before": (
                self._snapshot_legacy_autostart_entry()
            ),
            "autostart_entry_before": self._autostart_entry_exists(),
            "annotator": {
                "dconf_path": ANNOTATOR_DCONF_PATH,
                "override": self.settings.read_override(ANNOTATOR_DCONF_PATH),
                "ini_value": self._read_ini_annotator(),
            },
            "settings": {
                "custom": custom_values,
                "shell": shell_values,
                "bridge": {
                    "dconf_path": BRIDGE_DCONF_PATH,
                    "override": self.settings.read_override(BRIDGE_DCONF_PATH),
                },
            },
        }

    def _validate_state(self, state: Mapping[str, Any]) -> None:
        if state.get("format_version") != STATE_FORMAT_VERSION:
            raise BridgeError("unsupported activation state format")
        if state.get("uuid") != UUID:
            raise BridgeError("activation state belongs to a different UUID")
        if not isinstance(state.get("settings"), dict):
            raise BridgeError("activation state is missing settings")
        if state.get("phase") not in {
            "snapshotted",
            "bridge_staged",
            "enable_pending",
            "runtime_tainted",
            "extension_verified",
            "restart_required",
            "activated",
            "rolled_back",
        }:
            raise BridgeError("activation state has an invalid phase")
        try:
            settings = state["settings"]
            custom = settings["custom"]
            shell = settings["shell"]
            bridge = settings["bridge"]
            if set(custom) != {"binding", "command", "name"}:
                raise TypeError
            if not isinstance(shell, dict) or REQUIRED_SCREENSHOT_KEY not in shell:
                raise TypeError
            entries = [bridge, *custom.values(), *shell.values()]
            for entry in entries:
                if not isinstance(entry["dconf_path"], str):
                    raise TypeError
                if entry.get("override") is not None and not isinstance(
                    entry["override"], str
                ):
                    raise TypeError
            for entry in [*custom.values(), *shell.values()]:
                if not isinstance(entry["effective"], str):
                    raise TypeError
            if not isinstance(state["custom_registry"], str):
                raise TypeError
            if (
                "custom_registered_before" in state
                and not isinstance(state["custom_registered_before"], bool)
            ):
                raise TypeError
            # Absent in pre-autostart snapshots; rollback then leaves the
            # autostart entry untouched.
            autostart_before = state.get("autostart_entry_before")
            if autostart_before is not None and not isinstance(
                autostart_before, bool
            ):
                raise TypeError
            # Absent in snapshots created before the SnipSnap UUID migration;
            # rollback cannot safely infer the legacy extension's old state.
            if (
                "legacy_extension_enabled_before" in state
                and not isinstance(state["legacy_extension_enabled_before"], bool)
            ):
                raise TypeError
            # Absent in snapshots created before legacy desktop-entry
            # retirement was transactional; activation upgrades these before
            # changing either compatibility artifact.
            legacy_autostart = state.get("legacy_autostart_entry_before")
            if legacy_autostart is not None:
                self._validate_legacy_autostart_snapshot(legacy_autostart)
            # Absent in pre-annotator snapshots; rollback then leaves both
            # annotator flags untouched.
            annotator = state.get("annotator")
            if annotator is not None:
                if not isinstance(annotator["dconf_path"], str):
                    raise TypeError
                for field in ("override", "ini_value"):
                    field_value = annotator.get(field)
                    if field_value is not None and not isinstance(
                        field_value, str
                    ):
                        raise TypeError
            for field in (
                "prepared_payload",
                "enable_pending_payload",
                "runtime_payload",
            ):
                record = state[field]
                if record is None:
                    continue
                if set(record) != {"sha256", "shell_epoch"}:
                    raise TypeError
                if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                    raise TypeError
                epoch = record["shell_epoch"]
                if set(epoch) != {"session_id", "dbus_owner"}:
                    raise TypeError
                if not isinstance(epoch["session_id"], str) or not epoch["session_id"]:
                    raise TypeError
                if not isinstance(epoch["dbus_owner"], str) or not re.fullmatch(
                    r":[A-Za-z0-9_.-]+", epoch["dbus_owner"]
                ):
                    raise TypeError
            tainted_epoch = state["tainted_shell_epoch"]
            if tainted_epoch is not None:
                if set(tainted_epoch) != {"session_id", "dbus_owner"}:
                    raise TypeError
                if (
                    not isinstance(tainted_epoch["session_id"], str)
                    or not tainted_epoch["session_id"]
                ):
                    raise TypeError
                if not isinstance(tainted_epoch["dbus_owner"], str) or not re.fullmatch(
                    r":[A-Za-z0-9_.-]+", tainted_epoch["dbus_owner"]
                ):
                    raise TypeError
            if (
                state.get("phase") == "restart_required"
                and state["prepared_payload"] is None
            ):
                raise TypeError
            if (
                state.get("phase") == "enable_pending"
                and state["enable_pending_payload"] is None
            ):
                raise TypeError
            if (
                state.get("phase") in {"extension_verified", "activated"}
                and state["runtime_payload"] is None
            ):
                raise TypeError
        except (AttributeError, KeyError, TypeError) as error:
            raise BridgeError("activation state has invalid setting records") from error

    def _validate_snapshot_inputs(self, state: Mapping[str, Any]) -> None:
        settings = state["settings"]
        custom = settings["custom"]
        command = parse_variant(custom["command"]["effective"])
        binding = parse_variant(custom["binding"]["effective"])
        registry = parse_variant(str(state["custom_registry"]))
        custom_registered = state.get(
            "custom_registered_before", CUSTOM_PATH in registry
        )
        if not custom_registered:
            if CUSTOM_PATH in registry:
                raise BridgeError(
                    "clean snapshot disagrees with the custom-keybindings registry"
                )
            return
        if command != EXPECTED_CUSTOM_COMMAND:
            raise BridgeError(
                "custom SnipSnap command changed; expected "
                f"{EXPECTED_CUSTOM_COMMAND!r}, found {command!r}"
            )
        if binding != "Print":
            raise BridgeError(
                f"custom SnipSnap binding must be exactly 'Print', found {binding!r}"
            )
        if not isinstance(registry, list) or CUSTOM_PATH not in registry:
            raise BridgeError("the SnipSnap custom-keybinding path is not registered")

    @staticmethod
    def _read_regular_file(path: Path, *, expected_uid: int | None = None) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise BridgeError(
                f"cannot safely open regular file {path}: {error}"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise BridgeError(f"path is not a regular file: {path}")
            if expected_uid is not None and metadata.st_uid != expected_uid:
                raise BridgeError(f"installed file is not owned by this user: {path}")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                return stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _manifest_paths(raw: Any, *, field: str) -> tuple[str, ...]:
        if not isinstance(raw, list) or not raw:
            raise BridgeError(f"install manifest {field} must be a non-empty list")
        result: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                raise BridgeError(f"install manifest {field} entries must be strings")
            path = PurePosixPath(value)
            if (
                path.is_absolute()
                or path.as_posix() != value
                or ".." in path.parts
                or "\\" in value
            ):
                raise BridgeError(f"unsafe install manifest path: {value!r}")
            result.append(value)
        if len(result) != len(set(result)):
            raise BridgeError(f"install manifest {field} contains duplicates")
        return tuple(result)

    def _load_install_manifest(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        try:
            manifest = json.loads(
                self._read_regular_file(self.install_manifest).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError(f"cannot read install manifest: {error}") from error
        if not isinstance(manifest, dict):
            raise BridgeError("install manifest must contain a JSON object")
        if manifest.get("format-version") != 1:
            raise BridgeError("unsupported install manifest format")
        if manifest.get("uuid") != UUID:
            raise BridgeError("install manifest UUID does not match")
        if manifest.get("source-directory") != SOURCE_DIRECTORY:
            raise BridgeError("install manifest source directory does not match")
        payload = self._manifest_paths(manifest.get("files"), field="files")
        generated = self._manifest_paths(
            manifest.get("generated-files"), field="generated-files"
        )
        if set(generated) != {GENERATED_SCHEMA_PATH}:
            raise BridgeError(
                "install manifest may declare only schemas/gschemas.compiled "
                "as generated"
            )
        return payload, generated

    def _payload_digest(self, directory: Path) -> str:
        """Hash every manifest payload path and byte with unambiguous framing."""

        payload, _generated = self._load_install_manifest()
        expected_uid = (
            self.expected_file_uid
            if lexical_absolute(directory) == self.installed_dir
            else None
        )
        digest = hashlib.sha256(b"snipsnap-shell-bridge-payload-v1\0")
        for relative in sorted(payload):
            name = relative.encode("utf-8")
            content = self._read_regular_file(
                directory / relative, expected_uid=expected_uid
            )
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    @staticmethod
    def _expected_directories(files: set[str]) -> set[str]:
        directories: set[str] = set()
        for value in files:
            parent = PurePosixPath(value).parent
            while parent.as_posix() != ".":
                directories.add(parent.as_posix())
                parent = parent.parent
        return directories

    def _scan_installed_tree(self) -> tuple[set[str], set[str]]:
        try:
            root_metadata = self.installed_dir.lstat()
        except FileNotFoundError as error:
            raise BridgeError(
                f"installed extension directory is missing: {self.installed_dir}"
            ) from error
        if stat.S_ISLNK(root_metadata.st_mode):
            raise BridgeError(
                f"installed extension path must not be a symlink: {self.installed_dir}"
            )
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise BridgeError(
                f"installed extension path is not a directory: {self.installed_dir}"
            )
        if root_metadata.st_uid != self.expected_file_uid:
            raise BridgeError(
                f"installed extension is not owned by this user: {self.installed_dir}"
            )

        files: set[str] = set()
        directories: set[str] = set()
        pending = [self.installed_dir]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as error:
                raise BridgeError(
                    f"cannot inspect installed extension directory {directory}: {error}"
                ) from error
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(self.installed_dir).as_posix()
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise BridgeError(
                        f"cannot inspect installed extension entry {path}: {error}"
                    ) from error
                if stat.S_ISLNK(metadata.st_mode):
                    raise BridgeError(
                        f"installed extension contains a symlink: {relative}"
                    )
                if metadata.st_uid != self.expected_file_uid:
                    raise BridgeError(
                        f"installed extension entry is not user-owned: {relative}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    directories.add(relative)
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.add(relative)
                else:
                    raise BridgeError(
                        f"installed extension entry is not regular: {relative}"
                    )
        return files, directories

    def _verify_installed_tree(self, *, require_generated: bool) -> None:
        payload, generated = self._load_install_manifest()
        payload_set = set(payload)
        generated_set = set(generated)
        files, directories = self._scan_installed_tree()
        allowed_files = payload_set | generated_set
        extras = files - allowed_files
        missing_payload = payload_set - files
        missing_generated = generated_set - files if require_generated else set()
        expected_directories = self._expected_directories(allowed_files)
        extra_directories = directories - expected_directories
        missing_directories = expected_directories - directories
        if (
            extras
            or missing_payload
            or missing_generated
            or extra_directories
            or missing_directories
        ):
            raise BridgeError(
                "installed extension tree does not match manifest: "
                f"extra_files={sorted(extras)}, "
                f"missing_payload={sorted(missing_payload)}, "
                f"missing_generated={sorted(missing_generated)}, "
                f"extra_directories={sorted(extra_directories)}, "
                f"missing_directories={sorted(missing_directories)}"
            )

        for relative in payload:
            source_path = self.source_dir / relative
            installed_path = self.installed_dir / relative
            source_bytes = self._read_regular_file(source_path)
            installed_bytes = self._read_regular_file(
                installed_path, expected_uid=self.expected_file_uid
            )
            if installed_bytes != source_bytes:
                raise BridgeError(
                    f"installed payload differs from repository source: {relative}"
                )
        for relative in generated_set & files:
            self._read_regular_file(
                self.installed_dir / relative,
                expected_uid=self.expected_file_uid,
            )

    def _validate_metadata(self, extension_dir: Path) -> dict[str, Any]:
        expected_name = (
            UUID
            if lexical_absolute(extension_dir) == self.installed_dir
            else SOURCE_DIRECTORY
        )
        if extension_dir.name != expected_name:
            raise BridgeError(
                f"extension directory must be named {expected_name}: {extension_dir}"
            )
        metadata_path = extension_dir / "metadata.json"
        try:
            expected_uid = (
                self.expected_file_uid
                if lexical_absolute(extension_dir) == self.installed_dir
                else None
            )
            metadata = json.loads(
                self._read_regular_file(
                    metadata_path, expected_uid=expected_uid
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError(f"cannot read extension metadata: {error}") from error
        if metadata.get("uuid") != UUID:
            raise BridgeError("extension metadata UUID does not match")
        if metadata.get("shell-version") != SUPPORTED_SHELL_VERSION_DECLARATIONS:
            raise BridgeError(
                "extension metadata must target the audited GNOME Shell "
                "50 and 51 releases"
            )
        if metadata.get("session-modes") != ["user"]:
            raise BridgeError("extension metadata must target only the user session")
        if metadata.get("settings-schema") != BRIDGE_SCHEMA:
            raise BridgeError("extension metadata settings schema does not match")
        return metadata

    def _preflight_session(self) -> dict[str, str]:
        shell_version = self.executor.run(["gnome-shell", "--version"]).stdout.strip()
        match = re.search(r"GNOME Shell\s+(\d+)(?:\.|\s|$)", shell_version)
        if not match or int(match.group(1)) not in SUPPORTED_SHELL_MAJORS:
            raise BridgeError(
                "GNOME Shell major 50 or 51 is required; "
                f"found {shell_version!r}"
            )

        if self.environ.get("XDG_SESSION_TYPE") != "wayland":
            raise BridgeError("an active Wayland session is required")
        if "GNOME" not in self.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            raise BridgeError("an active GNOME desktop is required")
        if not self.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            raise BridgeError("the user D-Bus session address is missing")
        if not self.environ.get("WAYLAND_DISPLAY"):
            raise BridgeError("WAYLAND_DISPLAY is missing")
        session_id = self.environ.get("XDG_SESSION_ID")
        if not session_id:
            raise BridgeError("XDG_SESSION_ID is missing")

        result = self.executor.run(
            [
                "loginctl",
                "show-session",
                session_id,
                "--property=Class",
                "--property=Type",
                "--property=Active",
                "--property=LockedHint",
                "--no-pager",
            ]
        )
        properties: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
        expected = {
            "Class": "user",
            "Type": "wayland",
            "Active": "yes",
            "LockedHint": "no",
        }
        mismatches = {
            key: properties.get(key)
            for key, value in expected.items()
            if properties.get(key) != value
        }
        if mismatches:
            raise BridgeError(
                f"session is not an active, unlocked Wayland user session: {mismatches}"
            )
        return properties

    def _preflight(self, *, accept_installed: bool) -> None:
        self._ensure_unprivileged()
        self._validate_metadata(
            self.installed_dir if accept_installed else self.source_dir
        )
        self._preflight_session()

    def _save_phase(self, state: dict[str, Any], phase: str) -> None:
        state["phase"] = phase
        self.state_store.save(state)

    def _shell_epoch(self) -> dict[str, str]:
        result = self.executor.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.GetNameOwner",
                "org.gnome.Shell",
            ]
        )
        try:
            value = ast.literal_eval(result.stdout.strip())
        except (SyntaxError, ValueError) as error:
            raise BridgeError("cannot parse GNOME Shell D-Bus owner") from error
        if (
            not isinstance(value, tuple)
            or len(value) != 1
            or not isinstance(value[0], str)
            or not re.fullmatch(r":[A-Za-z0-9_.-]+", value[0])
        ):
            raise BridgeError("GNOME Shell returned an invalid D-Bus owner")
        return {
            "session_id": self.environ["XDG_SESSION_ID"],
            "dbus_owner": value[0],
        }

    @staticmethod
    def _payload_record(sha256: str, shell_epoch: Mapping[str, str]) -> dict[str, Any]:
        return {
            "sha256": sha256,
            "shell_epoch": dict(shell_epoch),
        }

    def _is_known(self) -> bool:
        result = self.executor.run(["gnome-extensions", "list"])
        return UUID in {line.strip() for line in result.stdout.splitlines()}

    def _is_enabled(self) -> bool:
        return self._is_extension_enabled(UUID)

    def _is_extension_enabled(self, uuid: str) -> bool:
        result = self.executor.run(["gnome-extensions", "list", "--enabled"])
        return uuid in {line.strip() for line in result.stdout.splitlines()}

    def _extension_state(self) -> int:
        result = self.executor.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell",
                "--object-path",
                "/org/gnome/Shell",
                "--method",
                "org.gnome.Shell.Extensions.GetExtensionInfo",
                UUID,
            ]
        )
        output = result.stdout.strip()
        matches = re.findall(
            r"(?:'|\")state(?:'|\")\s*:\s*<([0-9]+(?:\.[0-9]+)?)>",
            output,
        )
        if not matches:
            if re.fullmatch(r"\(\s*(?:@a\{sv\}\s*)?\{\s*\}\s*,?\s*\)", output):
                return EXTENSION_STATE_UNINSTALLED
            raise BridgeError("GNOME Shell extension info omitted its exact state")
        if len(matches) != 1:
            raise BridgeError("GNOME Shell extension info returned duplicate states")
        numeric = float(matches[0])
        if not numeric.is_integer():
            raise BridgeError("GNOME Shell extension state is not an integer")
        state = int(numeric)
        if state not in EXTENSION_STATE_NAMES:
            raise BridgeError(f"GNOME Shell returned unknown extension state {state}")
        return state

    @staticmethod
    def _extension_state_name(state: int) -> str:
        return EXTENSION_STATE_NAMES.get(state, f"UNKNOWN({state})")

    def _is_active(self) -> bool:
        return self._extension_state() == EXTENSION_STATE_ACTIVE

    def _compile_installed_schemas(self) -> None:
        schema_dir = self.installed_schema_dir
        if not schema_dir.is_dir() or not any(schema_dir.glob("*.xml")):
            raise BridgeError(
                f"installed extension schema XML is missing: {schema_dir}"
            )
        self.executor.run(["glib-compile-schemas", "--strict", str(schema_dir)])
        if not (schema_dir / "gschemas.compiled").is_file():
            raise BridgeError("glib-compile-schemas did not create gschemas.compiled")

    def _install_from_source(self) -> None:
        if not self.packager.is_file():
            raise BridgeError(f"extension packager is missing: {self.packager}")
        if not self.validator.is_file():
            raise BridgeError(f"extension validator is missing: {self.validator}")
        with tempfile.TemporaryDirectory(
            prefix="snipsnap-shell-bridge-bundle-"
        ) as temporary:
            bundle = Path(temporary) / f"{UUID}.shell-extension.zip"
            self.executor.run(
                [
                    sys.executable,
                    str(self.packager),
                    "--validator",
                    str(self.validator),
                    "--manifest",
                    str(self.install_manifest),
                    str(self.source_dir),
                    str(bundle),
                ]
            )
            self.executor.run(["gnome-extensions", "install", "--force", str(bundle)])

    def _install_or_accept(self, *, accept_installed: bool) -> None:
        if not accept_installed:
            self._install_from_source()
        self._verify_installed_tree(require_generated=False)
        self._validate_metadata(self.installed_dir)
        self._compile_installed_schemas()
        self._verify_installed_tree(require_generated=True)

    def _stage_safe_bridge_binding(self) -> None:
        # An override can survive an interrupted earlier experiment.  Force a
        # non-Print binding before enable so the verification barrier remains
        # meaningful even in that recovery case.  Rollback restores the exact
        # captured override, including absence.
        self._set_if_changed(
            BRIDGE_SCHEMA,
            BRIDGE_KEY,
            SAFE_BRIDGE_BINDING,
            schema_dir=self.installed_schema_dir,
        )

    def _setting_is(
        self,
        schema: str,
        key: str,
        desired: str,
        *,
        path: str | None = None,
        schema_dir: Path | None = None,
    ) -> bool:
        current = self.settings.get(schema, key, path=path, schema_dir=schema_dir)
        return parse_variant(current) == parse_variant(desired)

    def _set_if_changed(
        self,
        schema: str,
        key: str,
        desired: str,
        *,
        path: str | None = None,
        schema_dir: Path | None = None,
    ) -> None:
        if not self._setting_is(
            schema,
            key,
            desired,
            path=path,
            schema_dir=schema_dir,
        ):
            self.settings.set(
                schema,
                key,
                desired,
                path=path,
                schema_dir=schema_dir,
            )

    def _apply_print_bindings(self, state: Mapping[str, Any]) -> None:
        shell = state["settings"]["shell"]
        for key, snapshot in sorted(shell.items()):
            self._set_if_changed(
                SHELL_SCHEMA,
                key,
                remove_print_accelerator(snapshot["effective"]),
            )

        self._set_if_changed(
            CUSTOM_SCHEMA,
            "binding",
            "''",
            path=CUSTOM_PATH,
        )
        # Blanking the binding is not sufficient on Wayland: gsd-media-keys keeps
        # a live ShellKeyGrabber external grab for the custom keybinding that
        # outranks the extension's own Print keybinding, so Print keeps launching
        # the old command and the bridge is shadowed. Remove the entry from the
        # registry list so gsd destroys the keybinding object and drops the grab.
        registry = parse_variant(
            self.settings.get(CUSTOM_REGISTRY_SCHEMA, CUSTOM_REGISTRY_KEY)
        )
        if isinstance(registry, list) and CUSTOM_PATH in registry:
            self._set_if_changed(
                CUSTOM_REGISTRY_SCHEMA,
                CUSTOM_REGISTRY_KEY,
                format_variant([entry for entry in registry if entry != CUSTOM_PATH]),
            )
        self._set_if_changed(
            BRIDGE_SCHEMA,
            BRIDGE_KEY,
            "['Print']",
            schema_dir=self.installed_schema_dir,
        )
        self._recycle_extension()
        self._verify_active_bindings(state)

    def _recycle_extension(self) -> None:
        """Rebuild the extension's keybinding grab after Print is assigned.

        Activation deliberately enables the extension while the bridge key is
        still on the safe binding, so Print is never taken from its previous
        owner until the extension is proven ACTIVE. The cost is that moving the
        key to Print afterwards is a *live* reassignment, and on GNOME Shell 50
        that does not produce a working grab: mutter retracks the
        settings-backed binding off the same `changed::` signal the extension
        re-grabs on, so whichever runs last wins and it is not us. The
        extension's own re-grab reports a valid action id either way, so
        nothing downstream notices -- measured on the reference desktop
        2026-07-27, Print produced no `trigger` event at all until the
        extension was cycled.

        A disable/enable after the key is already Print makes enable()'s single
        addKeybinding bind Print directly, with no reassignment to lose.
        """
        if not self._is_enabled():
            return
        self.executor.run(["gnome-extensions", "disable", UUID])
        self.executor.run(["gnome-extensions", "enable", UUID])
        if not self._is_enabled():
            raise BridgeError("extension did not re-enable after the Print rebind")
        if not self._is_active():
            raise BridgeError("extension did not return to ACTIVE after the Print rebind")

    def _verify_active_bindings(self, state: Mapping[str, Any]) -> None:
        if (
            parse_variant(self.settings.get(CUSTOM_SCHEMA, "binding", path=CUSTOM_PATH))
            != ""
        ):
            raise BridgeError("custom SnipSnap Print binding is still active")
        registry = parse_variant(
            self.settings.get(CUSTOM_REGISTRY_SCHEMA, CUSTOM_REGISTRY_KEY)
        )
        if isinstance(registry, list) and CUSTOM_PATH in registry:
            raise BridgeError(
                "gsd custom SnipSnap keybinding is still registered; its stale "
                "grab would shadow the bridge Print binding"
            )
        for key in state["settings"]["shell"]:
            value = parse_variant(self.settings.get(SHELL_SCHEMA, key))
            if not isinstance(value, list) or "Print" in value:
                raise BridgeError(f"GNOME screenshot key still owns Print: {key}")
        if parse_variant(
            self.settings.get(
                BRIDGE_SCHEMA,
                BRIDGE_KEY,
                schema_dir=self.installed_schema_dir,
            )
        ) != ["Print"]:
            raise BridgeError("bridge did not acquire the Print binding")

    def _active_state_matches(self, state: Mapping[str, Any]) -> bool:
        try:
            if not self._is_enabled() or not self._is_active():
                return False
            self._verify_active_bindings(state)
            self.receiver_probe()
        except BridgeError:
            return False
        return True

    def _verify_runtime_proof(
        self,
        *,
        expected_digest: str,
        expected_shell_epoch: Mapping[str, str],
    ) -> None:
        """Re-attest disk bytes and Shell owner after ACTIVE read-back."""

        self._verify_installed_tree(require_generated=True)
        if self._payload_digest(self.installed_dir) != expected_digest:
            raise BridgeError("extension payload changed while it was being enabled")
        if self._shell_epoch() != dict(expected_shell_epoch):
            raise BridgeError(
                "GNOME Shell process changed while enabling the extension"
            )

    def _retire_legacy_extension(self) -> bool:
        """Disable the pre-rename UUID immediately before Print handover.

        The legacy bridge remains available until the replacement runtime and
        receiver are fully proven. Once handover starts, disabling it is a
        strict transaction step: silently leaving two enabled bridges would
        make Print ownership ambiguous.
        """
        if not self._is_extension_enabled(LEGACY_UUID):
            return False
        self.executor.run(["gnome-extensions", "disable", LEGACY_UUID])
        if self._is_extension_enabled(LEGACY_UUID):
            raise BridgeError(
                "legacy pre-rename shell bridge is still enabled; refusing "
                "Print handover"
            )
        return True

    def _restore_legacy_extension(self, state: Mapping[str, Any]) -> bool:
        """Restore the legacy UUID to the enabled state captured at snapshot."""

        if "legacy_extension_enabled_before" not in state:
            return False
        expected = state["legacy_extension_enabled_before"]
        current = self._is_extension_enabled(LEGACY_UUID)
        if current == expected:
            return False
        operation = "enable" if expected else "disable"
        self.executor.run(["gnome-extensions", operation, LEGACY_UUID])
        if self._is_extension_enabled(LEGACY_UUID) != expected:
            raise BridgeError(
                "rollback verification failed for the legacy shell bridge "
                "enabled state"
            )
        return True

    def activate(
        self, *, accept_installed: bool, dry_run: bool = False
    ) -> dict[str, Any]:
        self._ensure_unprivileged()
        existing = self.state_store.load()
        if existing is not None:
            self._validate_state(existing)

        if existing is None:
            state = self._capture_snapshot()
            if not dry_run:
                self.state_store.save(state)
        elif existing.get("phase") == "rolled_back":
            # A successful earlier activation proves which payload GNOME had
            # loaded. Preserve only that trust record while taking a new
            # binding snapshot, so user changes made after rollback are not
            # overwritten by the older snapshot.
            state = self._capture_snapshot()
            state["runtime_payload"] = copy.deepcopy(existing.get("runtime_payload"))
            if not dry_run:
                self.state_store.save(state)
        else:
            state = copy.deepcopy(existing)

        snapshot_upgraded = False
        if "legacy_extension_enabled_before" not in state:
            # Upgrade an in-progress snapshot from a manager that predates
            # legacy-state tracking. Capture the observable state before any
            # preflight or activation mutation so all subsequent handover
            # steps have a rollback record.
            state["legacy_extension_enabled_before"] = (
                self._is_extension_enabled(LEGACY_UUID)
            )
            snapshot_upgraded = True
        if "legacy_autostart_entry_before" not in state:
            state["legacy_autostart_entry_before"] = (
                self._snapshot_legacy_autostart_entry()
            )
            snapshot_upgraded = True
        if snapshot_upgraded and not dry_run:
            self.state_store.save(state)

        self._validate_snapshot_inputs(state)
        self._preflight(accept_installed=accept_installed)
        source_digest = self._payload_digest(self.source_dir)
        shell_epoch = self._shell_epoch()
        known_before = self._is_known()
        enabled_before = self._is_enabled()
        extension_state_before = self._extension_state()
        unsafe_loaded_before = (
            extension_state_before not in SAFE_DISABLED_EXTENSION_STATES
        )

        plan = [
            "retain owner-only binding snapshot",
            "accept installed extension"
            if accept_installed
            else "build and install user extension",
            "compile installed user schema",
            "require a fresh GNOME Shell process for every new payload",
            "stage bridge on its non-Print development binding",
            "enable and verify extension enabled plus ACTIVE",
            "authenticate the warm SnipSnap receiver",
            "ensure the SnipSnap daemon autostart entry",
            "enable the external annotator handoff when satty is present",
            "retire the legacy shell bridge after replacement verification",
            "retire the snapshotted pre-rename autostart entry at handover",
            "remove Print from existing screenshot bindings",
            "assign Print to bridge",
        ]
        if dry_run:
            if accept_installed:
                self._verify_installed_tree(require_generated=True)
            return {
                "action": "activate",
                "dry_run": True,
                "state_file": self.state_store.location,
                "extension_known": known_before,
                "extension_state": self._extension_state_name(extension_state_before),
                "plan": plan,
            }

        if state.get("phase") == "activated":
            self._verify_installed_tree(require_generated=True)
            runtime_payload = state.get("runtime_payload")
            if (
                runtime_payload is None
                or runtime_payload["sha256"] != source_digest
                or self._payload_digest(self.installed_dir) != source_digest
            ):
                raise BridgeError(
                    "activated extension payload changed; roll back before "
                    "preparing or activating an upgrade"
                )
            if self._active_state_matches(state):
                # Heal a missing autostart entry on rerun: an activated system
                # whose entry was removed (or that predates the entry) is one
                # login away from a dead handoff socket. Same for the external
                # annotator when satty was installed after activation.
                autostart_added = self._ensure_receiver_autostart()
                annotator_enabled = (
                    self.annotator_probe() and self._enable_external_annotator()
                )
                legacy_retired = (
                    "legacy_extension_enabled_before" in state
                    and self._retire_legacy_extension()
                )
                legacy_autostart_retired = (
                    "legacy_autostart_entry_before" in state
                    and self._retire_legacy_autostart_entry(state)
                )
                return {
                    "action": "activate",
                    "changed": (
                        autostart_added
                        or annotator_enabled
                        or legacy_retired
                        or legacy_autostart_retired
                    ),
                    "phase": "activated",
                    "state_file": self.state_store.location,
                }

        trusted_runtime = state.get("runtime_payload")
        trusted_digest = (
            trusted_runtime.get("sha256") if isinstance(trusted_runtime, dict) else None
        )
        prepared = state.get("prepared_payload")
        pending = state.get("enable_pending_payload")
        installed_digest: str

        if state.get("phase") == "enable_pending":
            if pending is None or pending["sha256"] != source_digest:
                raise BridgeError(
                    "enable-pending payload changed; refuse to load or move Print"
                )
            self._verify_installed_tree(require_generated=True)
            installed_digest = self._payload_digest(self.installed_dir)
            if installed_digest != source_digest:
                raise BridgeError("enable-pending extension payload digest changed")
            if pending["shell_epoch"] == shell_epoch:
                raise BridgeError(
                    "an enable attempt is pending in this GNOME Shell process; "
                    "restart GNOME Shell before retrying or moving Print"
                )
        elif state.get("phase") == "runtime_tainted":
            self._verify_installed_tree(require_generated=True)
            installed_digest = self._payload_digest(self.installed_dir)
            if installed_digest != source_digest:
                raise BridgeError("tainted runtime payload digest changed")
            tainted_epoch = state.get("tainted_shell_epoch")
            if tainted_epoch is None:
                state["tainted_shell_epoch"] = dict(shell_epoch)
                state["prepared_payload"] = self._payload_record(
                    installed_digest, shell_epoch
                )
                self._save_phase(state, "runtime_tainted")
                raise BridgeError(
                    "the failed runtime's Shell owner was unavailable; the "
                    "current Shell is now quarantined and must be restarted"
                )
            if tainted_epoch == shell_epoch:
                raise BridgeError(
                    "this GNOME Shell process has a tainted extension runtime; "
                    "restart GNOME Shell before retrying or moving Print"
                )
        elif trusted_digest == source_digest:
            self._verify_installed_tree(require_generated=True)
            installed_digest = self._payload_digest(self.installed_dir)
            if installed_digest != source_digest:
                raise BridgeError("trusted extension payload digest changed")
        elif state.get("phase") in {"restart_required", "bridge_staged"}:
            if prepared is None:
                raise BridgeError("restart-required state is missing its payload")
            if prepared["sha256"] != source_digest:
                if enabled_before or unsafe_loaded_before:
                    raise BridgeError(
                        "prepared extension source changed while the UUID is "
                        "loaded; disable or roll back it before preparing an upgrade"
                    )
                self._install_or_accept(accept_installed=accept_installed)
                installed_digest = self._payload_digest(self.installed_dir)
                state["prepared_payload"] = self._payload_record(
                    installed_digest, shell_epoch
                )
                self._save_phase(state, "restart_required")
                raise BridgeError(
                    "extension source changed while a restart was pending; a "
                    "new payload is prepared, so restart GNOME Shell and rerun "
                    "activation before Print can move"
                )

            self._verify_installed_tree(require_generated=True)
            installed_digest = self._payload_digest(self.installed_dir)
            if installed_digest != source_digest:
                raise BridgeError("prepared extension payload digest changed")
            if prepared["shell_epoch"] == shell_epoch:
                raise BridgeError(
                    "extension payload is prepared, but a fresh GNOME Shell "
                    "process is required before Print can move; log out, log "
                    "back in, and rerun activation"
                )
        else:
            if enabled_before or unsafe_loaded_before:
                raise BridgeError(
                    "an untrusted extension is enabled or in unsafe Shell state "
                    f"{self._extension_state_name(extension_state_before)}; "
                    "refusing to replace its files or move Print"
                )
            self._install_or_accept(accept_installed=accept_installed)
            installed_digest = self._payload_digest(self.installed_dir)
            state["prepared_payload"] = self._payload_record(
                installed_digest, shell_epoch
            )
            self._save_phase(state, "restart_required")
            known_detail = "already known to" if known_before else "newly installed for"
            raise BridgeError(
                f"the attested payload is {known_detail} this GNOME Shell; "
                "restart GNOME Shell and rerun activation before Print can move"
            )

        bindings_already_bridge = True
        try:
            self._verify_active_bindings(state)
        except BridgeError:
            bindings_already_bridge = False
        if bindings_already_bridge:
            # Print already routes to the bridge (e.g. an activate rerun after
            # the receiver died). Prove the receiver is alive BEFORE staging:
            # staging moves the bridge key off Print, so failing later at the
            # receiver probe would strip Print from every owner — the
            # 2026-07-22 reference-desktop incident's worst-case recovery path.
            self.receiver_probe()
        self._stage_safe_bridge_binding()
        self._save_phase(state, "bridge_staged")

        state["enable_pending_payload"] = self._payload_record(
            installed_digest, shell_epoch
        )
        self._save_phase(state, "enable_pending")
        if not self._is_enabled():
            self.executor.run(["gnome-extensions", "enable", UUID])

        # The digest-bound pending record is durable before enable. These
        # independent read-backs then prove that the intended load reached
        # ACTIVE before any existing Print owner changes.
        if not self._is_enabled():
            raise BridgeError("extension did not reach the enabled state")
        if not self._is_active():
            raise BridgeError("extension is enabled but did not reach ACTIVE state")
        try:
            self._verify_runtime_proof(
                expected_digest=installed_digest,
                expected_shell_epoch=shell_epoch,
            )
        except BridgeError as error:
            try:
                tainted_epoch: dict[str, str] | None = self._shell_epoch()
            except BridgeError:
                tainted_epoch = None
            state["runtime_payload"] = None
            state["tainted_shell_epoch"] = tainted_epoch
            state["prepared_payload"] = self._payload_record(
                installed_digest, tainted_epoch or shell_epoch
            )
            self._save_phase(state, "runtime_tainted")
            raise BridgeError(
                f"runtime provenance failed and this Shell is quarantined: {error}"
            ) from error
        state["tainted_shell_epoch"] = None
        state["runtime_payload"] = self._payload_record(installed_digest, shell_epoch)
        self._save_phase(state, "extension_verified")

        self.receiver_probe()
        # Enable autostart before Print moves: if this fails, the previous
        # Print owners are still fully functional.
        self._ensure_receiver_autostart()
        if self.annotator_probe():
            self._enable_external_annotator()
        # Keep the old bridge available through snapshot, preflight, payload
        # staging, ACTIVE read-back, provenance, and receiver authentication.
        # Retire it only at the final handover boundary, while all existing
        # Print owners are still intact and rollback can restore its state.
        self._retire_legacy_extension()
        self._retire_legacy_autostart_entry(state)
        self._apply_print_bindings(state)
        self._save_phase(state, "activated")
        return {
            "action": "activate",
            "changed": True,
            "phase": "activated",
            "state_file": self.state_store.location,
        }

    def _restore_snapshot(self, state: Mapping[str, Any]) -> None:
        settings = state["settings"]
        restore_entries = [settings["bridge"]]
        restore_entries.extend(settings["shell"].values())
        restore_entries.extend(settings["custom"].values())

        for snapshot in restore_entries:
            self.settings.restore_override(
                snapshot["dconf_path"], snapshot.get("override")
            )
        for snapshot in restore_entries:
            actual = self.settings.read_override(snapshot["dconf_path"])
            if actual != snapshot.get("override"):
                raise BridgeError(
                    "rollback verification failed for " f"{snapshot['dconf_path']}"
                )

        # Re-register the gsd custom keybinding entry that activation removed, so
        # the prior Print -> `snipsnap gui` shortcut is restored. Do this AFTER
        # the leaf binding/command/name keys are restored above, so gsd never
        # observes a registered entry with a blank accelerator.
        registry_snapshot = state.get("custom_registry")
        if registry_snapshot is not None:
            self.settings.set(
                CUSTOM_REGISTRY_SCHEMA,
                CUSTOM_REGISTRY_KEY,
                registry_snapshot,
            )
            if parse_variant(
                self.settings.get(CUSTOM_REGISTRY_SCHEMA, CUSTOM_REGISTRY_KEY)
            ) != parse_variant(registry_snapshot):
                raise BridgeError(
                    "rollback verification failed for the gsd "
                    "custom-keybindings registry list"
                )

        # Activation enabled the daemon autostart entry; remove it again only
        # when the snapshot proves it did not exist beforehand. Snapshots
        # without the field predate autostart management and are left alone.
        if (
            state.get("autostart_entry_before") is False
            and self._autostart_entry_exists()
        ):
            self.executor.run(["snipsnap", "config", "--autostart", "false"])
            if self._autostart_entry_exists():
                raise BridgeError(
                    "rollback could not remove the SnipSnap autostart entry"
                )

        # Restore both external-annotator flags exactly as snapshotted. Ini
        # first for the same reason enable writes gschema first: the daemon
        # must never be external while the extension is internal.
        annotator = state.get("annotator")
        if annotator is not None:
            if self._read_ini_annotator() != annotator.get("ini_value"):
                self._write_ini_annotator(annotator.get("ini_value"))
            self.settings.restore_override(
                annotator["dconf_path"], annotator.get("override")
            )
            if (
                self.settings.read_override(annotator["dconf_path"])
                != annotator.get("override")
            ):
                raise BridgeError(
                    "rollback verification failed for the external "
                    "annotator gschema key"
                )

    def _snapshot_is_restored(self, state: Mapping[str, Any]) -> bool:
        settings = state["settings"]
        entries = [settings["bridge"]]
        entries.extend(settings["shell"].values())
        entries.extend(settings["custom"].values())
        registry_snapshot = state.get("custom_registry")
        if registry_snapshot is not None and parse_variant(
            self.settings.get(CUSTOM_REGISTRY_SCHEMA, CUSTOM_REGISTRY_KEY)
        ) != parse_variant(registry_snapshot):
            return False
        if (
            state.get("autostart_entry_before") is False
            and self._autostart_entry_exists()
        ):
            return False
        annotator = state.get("annotator")
        if annotator is not None:
            if (
                self.settings.read_override(annotator["dconf_path"])
                != annotator.get("override")
            ):
                return False
            if self._read_ini_annotator() != annotator.get("ini_value"):
                return False
        if (
            "legacy_extension_enabled_before" in state
            and self._is_extension_enabled(LEGACY_UUID)
            != state["legacy_extension_enabled_before"]
        ):
            return False
        legacy_autostart = state.get("legacy_autostart_entry_before")
        if (
            legacy_autostart is not None
            and legacy_autostart["exists"]
            and self._snapshot_legacy_autostart_entry() != legacy_autostart
        ):
            return False
        return all(
            self.settings.read_override(snapshot["dconf_path"])
            == snapshot.get("override")
            for snapshot in entries
        )

    def rollback(self, *, dry_run: bool = False) -> dict[str, Any]:
        self._ensure_unprivileged()
        state = self.state_store.load()
        if state is None:
            raise BridgeError("no activation snapshot exists; refusing blind rollback")
        self._validate_state(state)
        enabled = self._is_enabled()
        extension_state = self._extension_state()
        safe_terminal = extension_state in SAFE_DISABLED_EXTENSION_STATES
        restored = self._snapshot_is_restored(state)
        if (
            state.get("phase") == "rolled_back"
            and not enabled
            and safe_terminal
            and restored
        ):
            return {
                "action": "rollback",
                "changed": False,
                "phase": "rolled_back",
                "state_file": self.state_store.location,
            }
        if dry_run:
            return {
                "action": "rollback",
                "dry_run": True,
                "state_file": self.state_store.location,
                "plan": [
                    "disable and verify extension is disabled and inactive",
                    "restore every captured dconf override exactly",
                    "remove the autostart entry if activation created it",
                    "restore the legacy shell bridge enabled state",
                    "restore the pre-rename autostart entry byte-for-byte",
                    "retain owner-only snapshot as rolled_back",
                ],
            }

        if enabled or not safe_terminal:
            self.executor.run(["gnome-extensions", "disable", UUID])
        if self._is_enabled():
            raise BridgeError("extension is still enabled; refusing binding restore")
        final_extension_state = self._extension_state()
        if final_extension_state not in SAFE_DISABLED_EXTENSION_STATES:
            raise BridgeError(
                "extension did not reach a safe disabled state; found "
                f"{self._extension_state_name(final_extension_state)}; "
                "refusing binding restore"
            )

        self._restore_snapshot(state)
        self._restore_legacy_extension(state)
        self._restore_legacy_autostart_entry(state)
        self._save_phase(state, "rolled_back")
        return {
            "action": "rollback",
            "changed": True,
            "phase": "rolled_back",
            "state_file": self.state_store.location,
        }

    def status(self) -> dict[str, Any]:
        state = self.state_store.load()
        if state is not None:
            self._validate_state(state)
        known = self._is_known()
        enabled = self._is_enabled()
        extension_state = self._extension_state()
        active = extension_state == EXTENSION_STATE_ACTIVE

        custom: dict[str, str] = {}
        for key in ("binding", "command", "name"):
            try:
                custom[key] = self.settings.get(CUSTOM_SCHEMA, key, path=CUSTOM_PATH)
            except BridgeError as error:
                custom[key] = f"unavailable: {error}"

        shell: dict[str, str] = {}
        try:
            keys = [
                key
                for key in self.settings.list_keys(SHELL_SCHEMA)
                if "screenshot" in key
            ]
            shell = {key: self.settings.get(SHELL_SCHEMA, key) for key in keys}
        except BridgeError as error:
            shell = {"error": str(error)}

        bridge: str | None
        try:
            bridge = self.settings.get(
                BRIDGE_SCHEMA,
                BRIDGE_KEY,
                schema_dir=self.installed_schema_dir,
            )
        except BridgeError:
            bridge = self.settings.read_override(BRIDGE_DCONF_PATH)

        receiver: dict[str, Any] = {"ready": True, "error": None}
        try:
            self.receiver_probe()
        except BridgeError as error:
            receiver = {"ready": False, "error": str(error)}

        return {
            "action": "status",
            "state_file": self.state_store.location,
            "snapshot_phase": state.get("phase") if state else None,
            "prepared_payload": state.get("prepared_payload") if state else None,
            "runtime_payload": state.get("runtime_payload") if state else None,
            "tainted_shell_epoch": (
                state.get("tainted_shell_epoch") if state else None
            ),
            "extension_known": known,
            "extension_enabled": enabled,
            "extension_active": active,
            "extension_state": self._extension_state_name(extension_state),
            "receiver": receiver,
            "autostart_entry": self._autostart_entry_exists(),
            "annotator": {
                "available": self.annotator_probe(),
                "config_value": self._read_ini_annotator(),
                "gschema_override": self.settings.read_override(
                    ANNOTATOR_DCONF_PATH
                ),
            },
            "bindings": {
                "custom": custom,
                "gnome_screenshot": shell,
                "bridge": bridge,
            },
        }


@dataclass(frozen=True)
class BridgePaths:
    source: Path
    installed: Path
    packager: Path
    install_manifest: Path
    validator: Path
    state_file: Path


def paths_for_script(script: Path, environ: Mapping[str, str]) -> BridgePaths:
    script = script.resolve()
    repository_root = script.parents[2]
    source = repository_root / "contrib" / "gnome-shell-extension" / SOURCE_DIRECTORY
    packager = script.with_name("package-extension.py")
    install_manifest = source.parent / "install-manifest.json"
    validator = repository_root / "tests" / "validate_gnome_shell_extension.py"
    if script.parent == Path("/usr/bin"):
        # Keep a pristine, uncompiled copy as the attested user-install source.
        # The system extension under /usr/share contains gschemas.compiled,
        # which must never be copied into a GNOME 50/51 extension ZIP.
        support = Path("/usr/lib/snipsnap/gnome-shell")
        source = support / "source" / SOURCE_DIRECTORY
        packager = support / "package-extension.py"
        install_manifest = support / "install-manifest.json"
        validator = support / "validate_gnome_shell_extension.py"
    home = Path(environ.get("HOME", str(Path.home()))).expanduser()
    data_home = Path(
        environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))
    ).expanduser()
    state_home = Path(
        environ.get("XDG_STATE_HOME", str(home / ".local" / "state"))
    ).expanduser()
    installed = data_home / "gnome-shell" / "extensions" / UUID
    state_file = state_home / "snipsnap-shell-bridge" / "activation-state.json"
    return BridgePaths(
        source=source,
        installed=installed,
        packager=packager,
        install_manifest=install_manifest,
        validator=validator,
        state_file=state_file,
    )


def default_paths(environ: Mapping[str, str]) -> BridgePaths:
    return paths_for_script(Path(__file__), environ)


def build_argument_parser(
    defaults: BridgePaths,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, default=defaults.state_file)
    parser.add_argument("--source-dir", type=Path, default=defaults.source)
    parser.add_argument("--installed-dir", type=Path, default=defaults.installed)
    parser.add_argument("--packager", type=Path, default=defaults.packager)
    parser.add_argument(
        "--install-manifest", type=Path, default=defaults.install_manifest
    )
    parser.add_argument("--validator", type=Path, default=defaults.validator)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument(
        "--installed",
        action="store_true",
        help="accept the existing user extension instead of reinstalling it",
    )
    activate_parser.add_argument("--dry-run", action="store_true")

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    environ = dict(os.environ)
    defaults = default_paths(environ)
    arguments = build_argument_parser(defaults).parse_args(argv)
    manager = ShellBridgeManager(
        SubprocessCommandExecutor(),
        FileStateStore(arguments.state_file),
        environ=environ,
        effective_uid=os.geteuid(),
        source_dir=arguments.source_dir,
        installed_dir=arguments.installed_dir,
        packager=arguments.packager,
        install_manifest=arguments.install_manifest,
        validator=arguments.validator,
    )
    try:
        if arguments.operation == "activate":
            result = manager.activate(
                accept_installed=arguments.installed,
                dry_run=arguments.dry_run,
            )
        elif arguments.operation == "rollback":
            result = manager.rollback(dry_run=arguments.dry_run)
        else:
            result = manager.status()
    except BridgeError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
