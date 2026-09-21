# GNOME Shell bridge activation

`snipsnap-shell-bridge` is the reversible, user-scoped tool that installs,
enables and verifies the GNOME Shell 50/51 bridge for the current user and
then moves `Print` to it. The Debian package installs it as
`/usr/bin/snipsnap-shell-bridge`; from a source checkout the same tool is
`python3 packaging/gnome-shell/manage_shell_bridge.py`. The examples below use
the packaged name.

The tool never uses `sudo`, `pkexec`, `apt` or `dpkg`, and it refuses to run
under them. It writes only to the current user's extension directory, dconf,
config and state directories. Do not run it over SSH or from a maintainer
script: run it from a terminal inside an active, unlocked GNOME 50 or 51
Wayland session, because it has to talk to that session's Shell and settings
daemon.

The package also installs a system copy of the extension under
`/usr/share/gnome-shell/extensions/`, which is inert until enabled, and a
pristine source copy under `/usr/lib/snipsnap/gnome-shell/source/` that the
tool builds the per-user bundle from. Every command prints one JSON document;
errors go to standard error with exit status 2.

## Read-only inspection

`status` reads the extension state and the three binding owners without
changing the extension, dconf or the snapshot. It reports known and enabled
state plus Shell's exact state (`ACTIVE`, `INACTIVE`, `ERROR` and so on),
any prepared and proven-runtime payload digests, and the result of the same
bounded receiver probe that gates moving `Print`:

```sh
snipsnap-shell-bridge status
```

Preview a source install and activation:

```sh
snipsnap-shell-bridge activate --dry-run
```

Preview activation of an extension already installed in
`$XDG_DATA_HOME/gnome-shell/extensions/`:

```sh
snipsnap-shell-bridge activate --installed --dry-run
```

A dry run performs the metadata, session, shortcut and Shell-version reads,
prints the plan, and does not build, install, compile, enable, write state or
alter settings. With `--installed` it also verifies the complete installed
tree and requires the compiled schema cache to exist already.

## Activate

Build the extension bundle, install it for the current user, compile its
schema, enable it, verify it, and move `Print`:

```sh
snipsnap-shell-bridge activate
```

A payload that this tool has not already proven is never enabled in the GNOME
Shell process that installed or attested it. The first run therefore exits
with a JSON error saying a restart is required and records the phase
`restart_required`; no `Print` owner has changed at that point. Log out, log
back in, and run the same command again. The second run re-attests the payload
under the new Shell D-Bus owner, enables it, requires the `ACTIVE` state, and
only then moves `Print`.

To accept an existing user installation instead of reinstalling the bundle:

```sh
snipsnap-shell-bridge activate --installed
```

The tool is idempotent. Re-running a completed activation verifies the
session, payload digest, enabled UUID, `ACTIVE` state and bindings, repairs a
missing daemon autostart entry or annotator setting, and otherwise exits
without another install or settings write. A rollback keeps the last proven
runtime digest, so the exact same payload may be reactivated without another
restart; a changed payload may not.

The activation barrier is deliberately ordered:

1. Capture effective values and exact dconf overrides for any custom
   `Print` → `snipsnap gui` media-key binding, every key containing
   `screenshot` in `org.gnome.shell.keybindings`, the bridge accelerator and
   the external-annotator key. If a custom binding exists it must be exactly
   `Print` running `snipsnap gui`; otherwise that step is skipped.
2. Atomically write that JSON snapshot as mode `0600` inside a mode `0700`
   state directory, rejecting symlinks at either path.
3. Require the expected UUID, Shell major `50` or `51`, metadata restricted to
   those versions and the `user` session mode, and an active, unlocked GNOME
   Wayland user session.
4. Build and install into, or accept, the current user's exact extension path
   (never a symlink). Verify that every directory and file is owned by the
   user, every file is regular, every declared payload byte matches the
   source, and nothing undeclared exists. The only generated file allowed is
   `schemas/gschemas.compiled`. Compile the schema and verify again.
5. Compute a canonical SHA-256 over the manifest paths and bytes. Record it
   with the current GNOME Shell D-Bus owner and require that owner to change
   before a new payload may load. An enabled or `ACTIVE` runtime with no
   matching provenance is rejected before any install or shortcut change.
6. Stage the bridge on `Super+Shift+S`, record a digest-bound `enable_pending`
   phase, enable the extension, and verify both the enabled-extensions list
   and Shell's own `ACTIVE` state.
7. After the `ACTIVE` read-back, re-attest every installed byte and re-read
   the Shell owner to close the enable-time race. Record the proven runtime
   digest and Shell process epoch.
8. Require the warm receiver at
   `$XDG_RUNTIME_DIR/snipsnap/gnome-shell-bridge-v1.sock`: every path
   component real, owner-only and owned by the user, the socket exactly mode
   `0600`. Connect with a 250 ms bound, read the peer credentials, and require
   that PID to own `tech.norvi.snipsnap` on the session bus.
9. Ensure the daemon's autostart entry (`snipsnap config --autostart true`,
   which writes `~/.config/autostart/SnipSnap.desktop`) so the receiver exists
   at the next login. If `satty` and `wl-copy` are both installed, enable the
   external-annotator handoff on both sides — the extension's
   `external-annotator` key and the daemon's `bridgeUseExternalAnnotator`
   configuration value.
10. Retire the pre-rename extension UUID and autostart entry if the snapshot
    recorded them.
11. Remove bare `Print` from GNOME's screenshot bindings and from any custom
    binding (blanking the binding is not enough on Wayland, so the custom
    entry is also removed from the media-keys registry), assign `Print` to
    the bridge, and cycle the extension once so it grabs `Print` directly.
    Verify the resulting bindings.

If tree attestation, runtime trust, enable verification or `ACTIVE`
verification fails, the existing screenshot bindings are untouched. The same
holds when the daemon is absent, the runtime path is unsafe, or the socket
peer is not the active SnipSnap D-Bus owner. A completed activation counts as
healthy only while that receiver proof continues to pass. A crash between
enable and verification leaves an explicit `enable_pending` phase; because
loaded extension bytes cannot be queried safely, retrying it requires a new
Shell process. A post-`ACTIVE` proof failure quarantines that Shell owner even
if the on-disk bytes are later repaired.

The default state file is:

```text
$XDG_STATE_HOME/snipsnap-shell-bridge/activation-state.json
```

With `XDG_STATE_HOME` unset that is
`~/.local/state/snipsnap-shell-bridge/activation-state.json`. The recorded
phase makes progress crash-safe: an interrupted activation can be retried or
rolled back from the original snapshot.

## Roll back

Preview:

```sh
snipsnap-shell-bridge rollback --dry-run
```

Roll back:

```sh
snipsnap-shell-bridge rollback
```

Rollback disables the extension, independently verifies that Shell reports
one of the safe terminal states `INACTIVE`, `INITIALIZED` or `UNINSTALLED`,
and restores every captured dconf override exactly, including keys that had
no override, the external-annotator settings, and a retired pre-rename
autostart entry. `DEACTIVATING`, `ERROR` and every other state fail closed
because extension cleanup may be incomplete.

Rollback refuses to guess when no snapshot exists. It keeps the protected
snapshot with phase `rolled_back`. A repeated rollback is a no-op while the
extension stays disabled and the overrides still match; otherwise it converges
them to the recorded state again. A later activation takes a fresh snapshot,
so changes made after a rollback are never replaced by old state.

## Development tests

The unit suite uses an in-memory state store and a fake command executor; its
receiver-probe cases add only a private temporary Unix socket. It does not
contact a real GNOME session or touch dconf:

```sh
python3 tests/manage_gnome_shell_bridge_test.py -v
```

It covers source installation, accepting an installed extension, owner-only
state permissions, symlink rejection, manifest byte attestation, extra or
unowned installed entries, preflight failure, dry-run and status read-only
behaviour, the enabled-but-`ERROR` state, rejection of an untrusted preloaded
runtime, fresh-Shell pending recovery, enable-time payload and Shell-owner
races, same-digest rollback reuse, changed-payload refusal, delayed-disable
and disable-to-`ERROR` rollback refusal, stale bridge overrides,
receiver-unavailable refusal before any `Print` change, exact rollback, and
repeated activate and rollback calls. On Linux it also exercises the real
receiver probe against secure and stale sockets, unsafe modes and path types,
invalid peer credentials, and matching, malformed or mismatched D-Bus-owner
PIDs.
