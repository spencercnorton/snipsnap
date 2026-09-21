## What changed

<!-- One paragraph. Link the issue if there is one: "Fixes #12". -->

## Why

## How it was tested

<!-- Paste the last lines of `ctest --test-dir build --output-on-failure --no-tests=error` (e.g. `100% tests passed, 0 tests failed`) and say that `for t in tests/*_test.py; do python3 "$t"; done` and `npm test` in contrib/gnome-shell-extension passed. If the change touches the bridge, the daemon's receiver or the capture editor, say whether you pressed the shortcut in a real GNOME Wayland session, on which GNOME Shell version, with how many monitors at which scales. Say which distro and Qt version (`qmake6 -query QT_VERSION`, or `dpkg -s qt6-base-dev | grep ^Version`). -->

## Checklist

- [ ] Configured with `-DENABLE_GNOME_SHELL_BRIDGE=ON -DSNIPSNAP_WARNINGS_AS_ERRORS=ON` and the build is warning-free; `ctest` passes ([recipe](https://github.com/spencercnorton/snipsnap/blob/main/CONTRIBUTING.md#working-on-the-code))
- [ ] The Python checks (`tests/*_test.py`) and the extension's `npm test` pass
- [ ] If the change touches `contrib/gnome-shell-extension/`, `src/core/shellbridge*` or `src/widgets/capture/`, a real GNOME Wayland session was exercised; headless tests do not cover the compositor path
- [ ] A change to the handshake wire format lands on both sides — `handoff-protocol.js` and `src/core/shellbridgeprotocol.cpp` — with tests on both
- [ ] `clang-format -i $(git ls-files '*.cpp' '*.h')` leaves the diff unchanged
- [ ] Commits are signed off (`git commit -s`, Developer Certificate of Origin)
- [ ] The version is not bumped (`CMakeLists.txt` and `packaging/debian/changelog` stay as they are; the maintainer bumps both at release)
- [ ] No secrets, machine names, or personal paths in the diff, and no screenshot of a real desktop
- [ ] Docs updated if behaviour changed: `README.md`, `docs/`, `data/man/man1/snipsnap.1` for CLI flags or environment variables, and `contrib/gnome-shell-extension/README.md` for the bridge

<!--
How this lands: this repository is a release mirror. A maintainer reviews the
pull request here, applies accepted changes to the development tree, and the
change ships in the next tagged release — the pull request is then closed
with a reference to that release. See CONTRIBUTING.md.
-->
