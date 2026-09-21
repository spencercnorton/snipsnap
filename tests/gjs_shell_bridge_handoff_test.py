#!/usr/bin/env python3
"""Exercise the real GJS/Gio handoff against authenticated AF_UNIX peers."""

from __future__ import annotations

import argparse
import os
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


SERVICE = "tech.norvi.snipsnap"
CAPTURE_ID = 0x0102030405060708
ACK_MAGIC = b"FSBRACK1"
COMMIT_MAGIC = b"FSBRCMT1"
SCENARIOS = (
    "normal",
    "lost-final-ack",
    "terminal-final-ack",
    "wrong-dbus-owner",
)
REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT = (
    REPO_ROOT
    / "contrib"
    / "gnome-shell-extension"
    / "tests"
    / "handoff-client.integration.mjs"
)


def own_service() -> Gio.DBusConnection:
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = connection.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "RequestName",
        GLib.Variant("(su)", (SERVICE, 4)),
        GLib.VariantType("(u)"),
        Gio.DBusCallFlags.NONE,
        1_000,
        None,
    )
    if reply.unpack()[0] != 1:
        raise RuntimeError("could not own the SnipSnap test service")
    return connection


def encode_ack(status: int) -> bytes:
    return struct.pack(">8sQIIQ", ACK_MAGIC, CAPTURE_ID, status, os.getpid(), 0)


def receive_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            break
        result.extend(part)
    return bytes(result)


def serve(scenario: str, owns_service: bool) -> None:
    service_connection = own_service() if owns_service else None
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    directory = runtime / "snipsnap"
    directory.mkdir(mode=0o700)
    path = directory / "gnome-shell-bridge-v1.sock"

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.settimeout(5)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(1)
        print("READY", flush=True)
        connection, _address = server.accept()
        with connection:
            connection.settimeout(5)
            if scenario == "wrong-dbus-owner":
                if receive_exact(connection, 1):
                    raise RuntimeError("unauthenticated client sent image bytes")
                return

            if receive_exact(connection, 3) != b"\x01\x02\x03":
                raise RuntimeError("request frame mismatch")
            connection.sendall(encode_ack(1))
            connection.sendall(encode_ack(2))
            commit = receive_exact(connection, 16)
            if commit != COMMIT_MAGIC + CAPTURE_ID.to_bytes(8, "big"):
                raise RuntimeError("commit frame mismatch")
            if scenario == "normal":
                connection.sendall(encode_ack(3))
            elif scenario == "terminal-final-ack":
                connection.sendall(encode_ack(0x80000004))

    del service_connection


def hold_service() -> None:
    connection = own_service()
    print("READY", flush=True)
    try:
        sys.stdin.buffer.read()
    finally:
        del connection


def wait_ready(process: subprocess.Popen[str], role: str) -> None:
    assert process.stdout is not None
    if process.stdout.readline().strip() != "READY":
        stderr = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"{role} failed to become ready: {stderr}")


def run_inside_bus(scenario: str) -> None:
    environment = os.environ.copy()
    owner: subprocess.Popen[str] | None = None
    if scenario == "wrong-dbus-owner":
        owner = subprocess.Popen(
            [sys.executable, __file__, "--hold-service"],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        wait_ready(owner, "D-Bus owner")

    server = subprocess.Popen(
        [
            sys.executable,
            __file__,
            "--serve",
            scenario,
            "--owns-service" if owner is None else "--no-owns-service",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wait_ready(server, "socket server")

    try:
        subprocess.run(
            ["gjs", "-m", str(CLIENT), scenario],
            check=True,
            env=environment,
            timeout=10,
        )
        server_stdout, server_stderr = server.communicate(timeout=10)
        if server.returncode != 0:
            raise RuntimeError(
                f"socket server failed: {server_stdout}{server_stderr}"
            )
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        if owner is not None:
            if owner.stdin is not None:
                owner.stdin.close()
            owner.wait(timeout=5)


def run_all() -> None:
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory() as runtime:
            os.chmod(runtime, 0o700)
            environment = os.environ.copy()
            environment["XDG_RUNTIME_DIR"] = runtime
            subprocess.run(
                [
                    "dbus-run-session",
                    "--",
                    sys.executable,
                    __file__,
                    "--inside-bus",
                    scenario,
                ],
                check=True,
                env=environment,
                timeout=20,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inside-bus", choices=SCENARIOS)
    mode.add_argument("--serve", choices=SCENARIOS)
    mode.add_argument("--hold-service", action="store_true")
    parser.add_argument("--owns-service", dest="owns_service", action="store_true")
    parser.add_argument(
        "--no-owns-service", dest="owns_service", action="store_false"
    )
    parser.set_defaults(owns_service=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.inside_bus:
        run_inside_bus(args.inside_bus)
    elif args.serve:
        serve(args.serve, args.owns_service)
    elif args.hold_service:
        hold_service()
    else:
        run_all()


if __name__ == "__main__":
    main()
