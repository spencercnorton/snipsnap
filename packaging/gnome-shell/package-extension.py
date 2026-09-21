#!/usr/bin/env python3
"""Build a deterministic, user-installable GNOME Shell extension ZIP."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


UUID = "snipsnap-shell-bridge@kleos.norvi.tech"
# The pristine source checkout is not named by UUID (a hostname in a path is
# awkward to ship); the installed extension directory always is.
SOURCE_DIRECTORY = "snipsnap-shell-bridge"
ALLOWED_SUFFIXES = {".css", ".js", ".json", ".mo", ".png", ".svg", ".ui", ".xml"}
ZIP_MINIMUM_EPOCH = 315532800  # 1980-01-01, the earliest ZIP timestamp.


def normalized_timestamp() -> tuple[int, int, int, int, int, int]:
    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", str(ZIP_MINIMUM_EPOCH))
    try:
        epoch = max(int(raw_epoch), ZIP_MINIMUM_EPOCH)
    except ValueError as error:
        raise RuntimeError("SOURCE_DATE_EPOCH must be an integer") from error
    return datetime.fromtimestamp(epoch, timezone.utc).timetuple()[:6]


def validate(source: Path, validator: Path, manifest: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(validator),
            "--manifest",
            str(manifest),
            str(source),
        ],
        check=True,
    )


def package_files(source: Path) -> list[Path]:
    result: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.as_posix() == "schemas/gschemas.compiled":
            raise RuntimeError(
                "generated gschemas.compiled must not be packaged for GNOME 50/51"
            )
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise RuntimeError(f"unrecognized extension packaging input: {relative}")
        result.append(relative)
    return sorted(result, key=lambda path: path.as_posix())


def build(source: Path, output: Path) -> None:
    timestamp = normalized_timestamp()
    files = package_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for relative in files:
                info = zipfile.ZipInfo(relative.as_posix(), timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info, (source / relative).read_bytes(), compresslevel=9
                )
        with zipfile.ZipFile(temporary_path) as archive:
            corrupt = archive.testzip()
            if corrupt is not None:
                raise RuntimeError(f"corrupt ZIP entry: {corrupt}")
            names = set(archive.namelist())
            if {"extension.js", "metadata.json"} - names:
                raise RuntimeError("extension ZIP is missing required root files")
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validator", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("extension_dir", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()

    source = arguments.extension_dir.resolve(strict=True)
    output = arguments.output.resolve()
    if source.name != SOURCE_DIRECTORY:
        parser.error(f"extension directory must be named {SOURCE_DIRECTORY}")
    if output.suffix != ".zip":
        parser.error("output must use the .zip suffix")

    try:
        repo_root = Path(__file__).resolve().parents[2]
        validator = (
            arguments.validator
            if arguments.validator is not None
            else repo_root / "tests" / "validate_gnome_shell_extension.py"
        ).resolve(strict=True)
        manifest = (
            arguments.manifest
            if arguments.manifest is not None
            else source.parent / "install-manifest.json"
        ).resolve(strict=True)
        validate(source, validator, manifest)
        build(source, output)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"extension bundle failed: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
