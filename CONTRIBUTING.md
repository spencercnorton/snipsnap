# Contributing to SnipSnap

Thanks for your interest. SnipSnap is a small project with one maintainer, so
the process is deliberately light — but a few things are fixed.

## How changes land

This GitHub repository is a **release mirror**: every commit on `main` is a
tagged release built from a private development tree, and `main` only ever
moves forward by a release. That has two consequences for contributors:

- Pull requests are reviewed **here**, but they are not merged here. An
  accepted change is applied to the development tree and ships in the next
  tagged release; the pull request is then closed with a reference to that
  release, and you keep the credit in the release notes.
- Please do not rebase your pull request onto anything but `main`.

## Before you start

- **Bugs** — open a [bug report](https://github.com/spencercnorton/snipsnap/issues/new/choose).
  A report with reproduction steps, versions and a scrubbed log excerpt is
  usually fixed faster than a pull request that arrives without one. The
  bridge logs a JSON timeline to your user journal
  (`journalctl --user -b -o cat | grep '\[snipsnap-shell-bridge\]'`); for
  the daemon's side of a bridge capture, stop the running daemon, start it in
  a terminal with `SNIPSNAP_CAPTURE_TRACE=stderr snipsnap` and press the
  shortcut (`SNIPSNAP_CAPTURE_TRACE=stderr snipsnap gui` traces the portal
  path only). Strip usernames, hostnames and window titles before pasting
  either.
- **Features** — open a feature request first. SnipSnap has strong opinions
  about capturing inside the compositor on GNOME (no portal prompt, a
  selection that spans every monitor), and about carrying no uploader, no
  update checker and no telemetry (see the README); an idea that cuts across
  them needs a conversation before code.
- **Security** — never in a public issue. Use
  [private vulnerability reporting](https://github.com/spencercnorton/snipsnap/security/advisories/new);
  see [SECURITY.md](SECURITY.md).

## Working on the code

Requirements: CMake 3.22+, a C++20 compiler, Qt 6.2 or newer, and — for
the extension and the Python checks — `gjs`, `dbus-daemon`, Node.js and
`python3-gi`. The first configure fetches Qt-Color-Widgets and
KDSingleApplication with `git`, so it needs network access once.

```bash
sudo apt install cmake git qt6-base-dev qt6-tools-dev qt6-tools-dev-tools qt6-svg-dev \
  qt6-l10n-tools libgl-dev dbus-daemon gjs libglib2.0-bin nodejs python3 python3-gi clang-format
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON \
  -DDISABLE_UPDATE_CHECKER=ON -DENABLE_IMGUR=OFF -DENABLE_GNOME_SHELL_BRIDGE=ON \
  -DSNIPSNAP_WARNINGS_AS_ERRORS=ON
cmake --build build --parallel
ctest --test-dir build --output-on-failure --no-tests=error   # C++ tests plus the Python tests wired into CTest
for t in tests/*_test.py; do python3 "$t" || break; done      # the Python checks, run as scripts (three need a built binary, gjs or gsettings)
(cd contrib/gnome-shell-extension && npm test)                  # node --test on the extension's unit tests
clang-format -i $(git ls-files '*.cpp' '*.h')                   # .clang-format; run before you commit
```

- Always configure with `-DENABLE_GNOME_SHELL_BRIDGE=ON`. Without it you get
  a build with no compositor capture path — the reason this program exists —
  and `ctest` still passes, so a green run proves nothing about the bridge.
- The extension and the daemon each hold half of the handshake. A change to
  the wire format in `contrib/gnome-shell-extension/*/handoff-protocol.js`
  needs the matching change in `src/core/shellbridgeprotocol.cpp`, and both
  test suites.
- The bridge is only exercised for real in a GNOME Wayland session. The unit
  tests and the private-bus portal tests run headless; say in the pull
  request whether you also pressed the shortcut on a real desktop, and with
  how many monitors at which scales.
- Do not bump the version. `SNIPSNAP_VERSION` in `CMakeLists.txt` and the top
  entry of `packaging/debian/changelog` must agree (a test enforces it); the
  maintainer bumps both at release.
- Keep a change to one concern. A pull request that fixes a bug and
  reformats a file is two pull requests.
- Tests: a bug fix carries a regression test; a feature carries the smallest
  test that fails without it.
- Commits carry a `Signed-off-by:` line (`git commit -s`, the Developer
  Certificate of Origin). There is no CLA.
- No secrets, hostnames, personal data or screenshots of a real desktop in
  the diff — the release process rejects them and the pull request will be
  sent back.

## Out of scope

So nobody wastes an evening on it, SnipSnap will not accept:

- an image-host uploader of any kind — SnipSnap uploads nothing anywhere
- an update checker — updates arrive through `apt`
- telemetry or analytics of any kind
- Windows or macOS packaging changes — the inherited CMake blocks are kept
  for reference, the packaging files are not published, those platforms are
  untested here and nothing ships for them
- replacing the GNOME Shell bridge with the desktop portal on GNOME — the
  portal stays as the fallback for other compositors, not as the GNOME path

## Pull request checklist

The template asks for what changed, why, and how it was tested, plus a
confirmation that the diff carries no secrets, machine names or personal
paths. Fill it in — it is what the reviewer reads first.

## Licence

By contributing you agree that your contribution is licensed under the
[GNU General Public License, version 3 or later](LICENSE) that covers the
project.
