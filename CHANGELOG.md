# Changelog

## 2.0.4 — 2026-09-27

- Build and install-test Debian packages on Ubuntu 26.04, avoiding the
  incompatible Qt dependency names from Ubuntu 24.04 packages.


One entry per tagged release, newest first; the entry is the GitHub Release
body. Versions before 1.0.0 were published under the upstream name and are
recorded in `packaging/debian/changelog`.

## 2.0.3 — 2026-09-27

- Establish GitHub pull requests as the development workflow, with privacy checks.
- Add deployment, configuration, security, upgrade and recovery documentation.

## 2.0.2 — 2026-09-21

First public release on GitHub. No change to the program: this is 2.0.1 with
its identity and URLs pointed at this repository, and the bridge activation
tool reading its pristine extension copy from a fixed directory name.

- Published the source tree at github.com/spencercnorton/snipsnap as a release
  mirror; the `.deb` still comes from the apt repository the README describes.
- Added the README for this repository, with captures of the overlay and
  the editor from a headless GNOME Shell session.
- The source tarball served beside the `.deb` on the apt repository is now
  the same exported tree that is published here, not an archive of the
  development checkout.
- Added the community files: CONTRIBUTING, SECURITY, SUPPORT, a Code of
  Conduct, NOTICE, issue forms and a pull-request template.
- Replaced the inherited Code of Conduct, which carried an e-mail address,
  with one that routes reports through private vulnerability reporting.
- Scrubbed development-tree identifiers and URLs from the metadata, packaging
  and documentation; nothing about how the program runs changed.

## 2.0.1 — 2026-09-10

- Desktop notifications now identify themselves as SnipSnap. They carried no
  desktop-entry hint, so GNOME could not match them to an installed
  application: the notification list showed the raw process name, offered no
  per-app settings, and every capture opened its own notification group.
  Taking six screenshots left six separate entries to dismiss.

## 2.0.0 — 2026-08-12

- Everything the program calls itself is now SnipSnap: the binary is
  `/usr/bin/snipsnap`, the D-Bus name is `tech.norvi.snipsnap`, the desktop
  and AppStream IDs match, the icons are named for it, and the GNOME Shell
  extension is the SnipSnap Shell Bridge. Until now only the display strings
  had been renamed.
- Settings survive the rename. The configuration moved to
  `~/.config/snipsnap`; the first run copies the old directory across rather
  than starting from defaults, and leaves the old tree untouched so a
  downgrade still finds it.
- `snipsnap-shell-bridge activate` transactionally installs and verifies the
  per-user GNOME bridge, migrates ownership of <kbd>Print</kbd>, and retires
  the previous extension and autostart entry only after the new bridge and
  receiver are proven active. Rollback restores the exact previous state if
  any later step fails.
- The compositor-backed Wayland editor now exposes the complete native
  annotation toolbar, with live GNOME palette changes, high-contrast icons,
  copy, save, pan, fit and zoom controls.
- The desktop portal stays as the bounded fallback for other Wayland
  compositors; GNOME uses the prompt-free Shell bridge for multi-monitor
  region capture. The package installs the native Qt Wayland and GTK platform
  integrations so it follows the desktop theme, declares every build and
  bridge runtime dependency, and audits the bridge against pinned GNOME Shell
  and Mutter 51 sources.
- Removed inherited upstream material: the upstream README, developer site,
  RFC process, snapcraft recipe and documentation PDF. `/usr/bin/flameshot`
  remains as a compatibility symlink, and the package still conflicts with,
  replaces and provides the upstream package so an apt upgrade can never
  silently replace a distribution's own copy.

## 1.0.3 — 2026-07-27

- Declare `python3` and `libglib2.0-bin` as dependencies. `snipsnap-annotator`
  is a Python script that drives `gsettings`, and on a machine with only the
  declared dependencies it would have died on its shebang. A test now checks
  the script's interpreter and every command it invokes against the package
  control file.
- Fixed the package Homepage URL, which returned 404 in `apt show` and in
  GNOME Software.
- Dropped the suggested `ca-certificates` and `openssl` packages: nothing in
  this package speaks TLS. The image uploader and the update checker are both
  compiled out.

## 1.0.2 — 2026-07-27

- Report the version the package actually is. The binary said 1.0.0 while apt
  said 1.0.1, and 1.0.1 existed only to fix the dead capture shortcut, so
  `snipsnap --version` could not tell you whether you had that fix. A test now
  fails if `CMakeLists.txt` and the package changelog disagree.
- Point the AppStream metadata at this project instead of the upstream
  tracker, and stop advertising the removed image uploader.
- Finish the rename in the settings window, the CLI help and error text, and
  the tray notification titles.
- Update the translations so the renamed strings are still translated; they
  key off the English source text, so every renamed string had been falling
  back to English in all 43 locales.

## 1.0.1 — 2026-07-27

- Cycle the Shell extension after activation moves the capture shortcut to
  <kbd>Print</kbd>. On GNOME Shell 50 a live reassignment does not produce a
  working grab, and every existing check passed anyway: the keybinding call
  returned a valid action id, the extension logged the re-grab as successful,
  and the activation tool only ever compared dconf values. Measured on the
  reference desktop: no trigger event was logged for any <kbd>Print</kbd>
  press between activation and a manual disable/enable.
- Log a rejected capture instead of discarding it. The keybinding handler had
  nobody above it to catch a throw, so a failure anywhere in the capture path
  produced an unresponsive shortcut and an empty journal.
- Document that changing the shortcut by hand needs the extension restarted.

## 1.0.0 — 2026-07-26

- First release under the SnipSnap name. The binary package is `snipsnap`;
  it conflicts with, replaces and provides the upstream package, because the
  two share `/usr/bin/flameshot`, the upstream D-Bus name and
  `~/.config/flameshot` and cannot be co-installed — but nothing is hijacked
  on a machine that never added this repository.
- The version series restarts at 1.0.0, without the epoch the inherited
  package carried.
- The GNOME Shell bridge ships in the package: the extension and its compiled
  schema install under `/usr/share/gnome-shell/extensions`, and the receiver
  is always compiled in.
- `/usr/bin/snipsnap` works as a command without breaking existing
  keybindings and scripts.
- Added `snipsnap-annotator`, which switches the external annotator on and
  off on both sides of the handshake at once; setting only one of the two
  keys hangs the next capture waiting for an editor window that never opens.
