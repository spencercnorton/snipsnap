#!/usr/bin/env bash
# Produce the public README pictures in the headless GNOME Shell 50 rig.
#
# Runs tests/gnome_shell_bridge_demo.js through the same wrappers as the
# end-to-end test (tests/gnome_shell_bridge_e2e.js), inside a container with
# the pinned Ubuntu 26.04 image, apt snapshot and package set (plus gnome-text-editor,
# gnome-calculator, qt6-svg-plugins, python3-pil). Expects a built
# build-gnome-e2e/src/snipsnap, the gnome-shell 50.1 checkout in
# GNOME_SHELL_TEST_SOURCE and a wallpaper in SHELL_BACKGROUND_IMAGE.
#
#   DEMO_OUTPUT_DIR=/out/demo scripts/demo-capture.sh
set -euo pipefail
cd "$(dirname "$0")/.."

out=${DEMO_OUTPUT_DIR:-/out/demo}
rm -rf "$out"
install -d -m 0755 "$out"

export GNOME_SHELL_TEST_SOURCE=${GNOME_SHELL_TEST_SOURCE:-/opt/gnome-shell-50.1}
export SHELL_BACKGROUND_IMAGE=${SHELL_BACKGROUND_IMAGE:-/assets/wallpaper.jpg}
export SNIPSNAP_TEST_EXECUTABLE=${SNIPSNAP_TEST_EXECUTABLE:-$PWD/build-gnome-e2e/src/snipsnap}
export SNIPSNAP_REAL_PERF_HELPER=${SNIPSNAP_REAL_PERF_HELPER:-/usr/libexec/gnome-shell-perf-helper}
export GNOME_SHELL_BUILDDIR=$PWD/tests/gnome-shell-perf-helper
export LIBGL_ALWAYS_SOFTWARE=1
export META_DBUS_RUNNER_DISABLE_LOGIND_PASSTHROUGH=1
export META_DBUS_RUNNER_DISABLE_UMOCKDEV=1
export GNOME_SHELL_SESSION_MODE=user
# CI's fatal-criticals would take the shell down on a GTK app warning.
unset G_DEBUG

extension=contrib/gnome-shell-extension/snipsnap-shell-bridge
python3 packaging/gnome-shell/package-extension.py "$extension" "$out/snipsnap-shell-bridge.zip"

# The demo is longer than the e2e (two themes, ~60 frames): 6m instead of 2m.
python3 tests/run_gnome_shell_e2e_with_startup_retry.py --output-dir "$out" -- \
  timeout --signal=TERM --kill-after=30s 6m \
  python3 tests/run_gnome_shell_bridge_e2e.py -- \
  /usr/bin/gnome-shell-test-tool --headless --disable-animations \
  --extra-filter=snipsnap \
  --extra-filter=org.gnome.TextEditor \
  --extra-filter=org.gnome.Calculator \
  --extension "$out/snipsnap-shell-bridge.zip" \
  tests/gnome_shell_bridge_demo.js

python3 scripts/strip_png.py "$out"
grep -E 'DEMO-(DONE|FAIL|PROBLEMS)' "$out/e2e.log" || true
