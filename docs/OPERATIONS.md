# SnipSnap deployment and operations

SnipSnap captures and annotates screenshots on Linux. On GNOME Wayland the
Shell bridge owns selection; the daemon owns decoding, editing and saving.
Other Wayland compositors use the screenshot portal. The portable ScreenCast
backend is not part of the stable release.

## Requirements and installation

The published package targets Ubuntu 26.04 on amd64. Use a normal GNOME
Wayland user session for the Shell bridge. Confirm your session with
`echo "$XDG_SESSION_TYPE"` and record `gnome-shell --version` before diagnosing
extension compatibility.

For a manual GitHub install, use v2.0.4 or newer: these Debian packages are
built and install-tested on Ubuntu 26.04. Download the `.deb`, `source.tar.gz`
and `SHA256SUMS.txt` from the same [release](https://github.com/spencercnorton/snipsnap/releases)
into an empty directory, then verify all downloaded bytes before installation:

```bash
sha256sum --check SHA256SUMS.txt
sudo apt install ./snipsnap_2.0.4-1_amd64.deb
snipsnap --version
```

Version 2.0.3's Debian asset was built on Ubuntu 24.04 and cannot satisfy its
Qt dependency on 26.04. Its tag and assets remain available for historical
reference. GitHub installs are versioned manual updates; the signed APT feed
publishes on its own schedule. Use the APT route for managed upgrades:

```bash
curl -fsSL https://apt.globalentry.systems/setup.sh -o /tmp/norvitech-apt-setup.sh
# Read the downloaded repository setup script before running it.
less /tmp/norvitech-apt-setup.sh
sudo sh /tmp/norvitech-apt-setup.sh
sudo apt install snipsnap
snipsnap-shell-bridge activate
```

The package replaces Flameshot because it owns the compatibility command.
The first bridge activation verifies the extension before taking over Print.
If activation requests a session restart, log out and back in, then activate
again. Do not restart GNOME Shell in a live Wayland session.

## First capture and configuration

Press Print, select a region, release and press Enter. Add annotations, then
copy or save. Check a second monitor and a mixed-scale layout if you use them.
The portal path is also available through `snipsnap gui`.

```bash
snipsnap --version
snipsnap-shell-bridge status
snipsnap config
snipsnap config --check
```

Configure tools, colors, save location and filename pattern in the settings
window. Settings live in `~/.config/snipsnap/snipsnap.ini`. Prefer a private
save directory when captures may contain documents or account information.
SnipSnap does not provide an uploader or a network update checker.

![A demo capture from selection through annotation](screenshots/flow.png)

## Security and backup

The GNOME extension can see the unlocked session's pixels; install it only
from a source you trust. The bridge uses an owner-only runtime socket and
refuses locked-session captures. Saved images and the clipboard may contain
private data even though the program does not send them over the network.

Back up your settings and the directories where you save captures. The
runtime socket and cache are temporary state, not backup targets. Review
image pixels and metadata before sharing. Blur or remove account names,
notifications, URLs and document text in published examples.

## Upgrades and verification

Upgrade with your package manager. Confirm the installed version and bridge
status, then capture, copy and save a test region. Verify both light and dark
editor appearance and all monitors. Keep the previous package if you need a
rollback; save the current configuration before reinstalling it.

## Troubleshooting

| Symptom | Check and next action |
|---|---|
| Print opens GNOME's capture UI | Run bridge status and activation; check for a shortcut conflict. |
| Extension is installed but inactive | Confirm the supported Shell version; complete a requested logout/login. |
| Portal permission dialog appears | Confirm GNOME bridge activation; other compositors use the portal. |
| Capture cannot be saved | Check target-directory permissions and free disk space. |
| A monitor selection is wrong | Report display scale, rotation and topology with synthetic captures. |

For bridge diagnostics, use a scrubbed user-journal excerpt. Do not post the
complete desktop journal or a screenshot of a real workspace. See
[the bridge guide](gnome-shell-bridge.md) and [architecture](architecture.md)
for protocol and backend details.
