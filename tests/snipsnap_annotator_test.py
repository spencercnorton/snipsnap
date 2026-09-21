#!/usr/bin/env python3
"""snipsnap-annotator writes both halves of the handshake, and only those.

The failure this guards against is silent: writing one key and not the other
leaves the next capture waiting for an editor window that never opens.

Runs itself under `dbus-run-session` because dconf is not isolated by
XDG_CONFIG_HOME alone -- `gsettings set` goes through whichever dconf-service
owns the session bus, so a write would land in the real user database while the
read came from the temporary one.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "packaging" / "snipsnap-annotator"
UUID = "snipsnap-shell-bridge@kleos.norvi.tech"
SCHEMA_SOURCE = REPO / "contrib" / "gnome-shell-extension" / "snipsnap-shell-bridge" / "schemas"
SANDBOX_ENV = "SNIPSNAP_ANNOTATOR_TEST_ROOT"


def reexec_isolated():
    """Build the sandbox, then hand it to a copy of ourselves on a private bus."""
    root = Path(tempfile.mkdtemp(prefix="snipsnap-annotator-test."))
    try:
        schemas = root / "share" / "gnome-shell" / "extensions" / UUID / "schemas"
        schemas.mkdir(parents=True)
        for xml in SCHEMA_SOURCE.glob("*.gschema.xml"):
            (schemas / xml.name).write_bytes(xml.read_bytes())
        subprocess.run(["glib-compile-schemas", "--strict", str(schemas)], check=True)
        (root / "config").mkdir()

        environment = dict(os.environ)
        environment[SANDBOX_ENV] = str(root)
        environment["XDG_DATA_HOME"] = str(root / "share")
        environment["XDG_CONFIG_HOME"] = str(root / "config")
        return subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve())],
            env=environment,
        ).returncode
    finally:
        shutil.rmtree(root, ignore_errors=True)


def load():
    spec = importlib.util.spec_from_loader(
        "snipsnap_annotator",
        importlib.machinery.SourceFileLoader("snipsnap_annotator", str(SCRIPT)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run():
    module = load()
    root = Path(os.environ[SANDBOX_ENV])
    schemas = root / "share" / "gnome-shell" / "extensions" / UUID / "schemas"

    # A per-user extension shadows the packaged one in GNOME, so it has to win
    # here too, or the helper writes a key the running extension never reads.
    assert module.schema_dir() == schemas, module.schema_dir()
    assert module.DEPENDENCIES == ("satty", "wl-copy"), module.DEPENDENCIES

    ini = module.ini_path()
    ini.parent.mkdir(parents=True)
    # Unrelated keys must survive, including a value with a literal % --
    # configparser's default interpolation raises on it.
    ini.write_text("[General]\nsavePath=/tmp/100% sure\nstartupLaunch=true\n")

    module.write_state(True)
    assert module.read_state() == (True, True), module.read_state()

    module.write_state(False)
    assert module.read_state() == (False, False), module.read_state()

    text = ini.read_text()
    assert "startupLaunch=true" in text, text
    assert "savePath=/tmp/100% sure" in text, text
    assert "bridgeUseExternalAnnotator=false" in text, text

    # A half-written state is the whole reason this script exists, so `status`
    # has to report it as a failure rather than shrug.
    subprocess.run(
        ["gsettings", "--schemadir", str(schemas), "set", module.SCHEMA,
         module.KEY, "true"],
        check=True,
    )
    assert module.read_state() == (True, False), module.read_state()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "status"], capture_output=True, text=True
    )
    assert result.returncode == 1, result
    assert "disagree" in result.stderr, result.stderr

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(run() if SANDBOX_ENV in os.environ else reexec_isolated())
