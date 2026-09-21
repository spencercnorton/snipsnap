# SnipSnap GNOME Shell Bridge

This GNOME Shell 50 and 51 extension freezes the compositor stage in memory
and immediately shows one selection surface across every monitor. The initial
capture does not call the Screenshot portal, encode a PNG, flash the display,
play a capture sound or write an image to disk.

The bridge implements:

- a configurable, Shell-owned accelerator;
- unlocked-user-session and topology guards;
- full-stage frozen content with a dimmed selection overlay that follows the
  desktop accent colour on GNOME 47 and later;
- pointer selection across monitor seams, a dimensions label, Escape and
  Enter;
- selected-region-only PNG encoding after Enter, capped at 32 million pixels
  and 128 MiB;
- an immutable topology snapshot that requires Mutter's normalised,
  non-overlapping, edge-connected monitor layout, exact Clutter `StageView`
  scales and exact stage-texture dimensions;
- a pre-capture 32-million-pixel / 32,768-per-axis bound on the whole-stage
  compositor allocation;
- an owner-authenticated Unix-socket handoff to the SnipSnap daemon, with
  request-accepted, editor-ready and commit-accepted phases;
- D-Bus activation of the daemon when nothing is listening yet;
- focus transfer only to a new normal SnipSnap window observed after peer
  authentication, with the daemon PID bound to kernel socket credentials and
  an exact capture-ID-bearing title;
- monotonic JSON logs for trigger, capture-ready, first-paint and selection;
- deterministic resource cleanup on cancel, lock, monitor change or disable.

Enter encodes only the outward-rounded selected rectangle from the retained
stage texture, then sends one bounded binary request to
`$XDG_RUNTIME_DIR/snipsnap/gnome-shell-bridge-v1.sock`. If the socket is
missing or refuses the connection, the client asks the session bus to start
`tech.norvi.snipsnap` and retries a bounded number of times; it never spawns a
process itself. The client accepts only a same-UID Unix peer, requires that
peer PID to own `tech.norvi.snipsnap` on the session bus at connection time,
and requires every acknowledgement's daemon PID to match the kernel-reported
socket peer PID. After `EDITOR_READY`, Shell requires a new normal SnipSnap
window created during the request with the same PID and the exact title
`SnipSnap [capture-id=<uint64>]`. Only then does it send the 16-byte commit
frame and require `COMMIT_ACCEPTED`. The daemon closes its staged editor if the
socket disappears before commit. Once committed, Shell releases the overlay,
then revalidates and focuses the exact editor in Mutter's `BEFORE_REDRAW`
phase after its compositor actor is mapped. The bounded focus handoff retries
for at most eight compositor frames and is cancelled on a new capture, a
lock or session transition, or extension disable. Shell deliberately does not
synchronously re-raise the newly created window because it may not yet have a
Mutter stack slot. Escape remains available through staging and is disabled
only during the final commit/ACK exchange.

Once commit transmission begins, the client can no longer prove that the
complete frame did not enter the local socket. Any subsequent failure is
therefore conservatively irrevocable: Shell logs `commit-ack-indeterminate`,
releases the overlay, and exposes the already-authenticated editor instead of
offering a duplicate retry. The compositor readback promise cannot be
cancelled after it starts, so a module-lifetime ownership gate remains held
even if Escape, lock or disable removes the overlay, and a new capture is
refused until that exact encode settles. Failures before commit leave the
frozen overlay available for retry or Escape, and a `BUSY` refusal tells the
user to close the open SnipSnap editor first. The extension does not expose a
D-Bus service, access a regular file, launch a process, or use a network
socket.

When the `external-annotator` setting is on, the daemon hands committed
captures to an external annotator (`satty`) instead of opening its own editor,
and the extension commits without waiting for an editor window. The activation
tool keeps this key and the daemon's configuration in step; do not enable one
side alone.

The native editor is presented through one compositor-bounded
`RegionEditorWindow`; it does not attempt to make an ordinary Wayland
`xdg_toplevel` span the virtual desktop. The selected image remains an exact
full-size `CaptureWidget` document embedded through `QGraphicsProxyWidget`.
The host initially fits that entire canvas with letterboxing and supplies
unscaled Fit, 1:1, zoom and middle-drag pan controls, plus a native strip for
the annotation tools, colour, size, Copy and Save. Annotation and export
coordinates therefore remain in the original logical selection space even
when Mutter limits the visible top-level to one output or work area.

The socket and D-Bus PID checks prevent cross-user access and accidental
same-user receiver substitution. They do not authenticate executable identity
against a fully hostile process already running as the desktop user: such a
process could claim the well-known name after the real daemon exits. Treating
same-UID code as hostile would require an LSM-confined helper or privileged
broker beyond this unprivileged per-user design.

The audited GNOME 50/51 compositor API has one known latency boundary:
`screenshot_stage_to_content()` paints one whole-stage texture synchronously.
Logical layout mode uses the maximum monitor `geometry_scale`; physical layout
mode uses scale 1 even when configured monitor scales are larger. The bridge
reads the public `global.stage.peek_stage_views()` list and each view's
`get_scale()` immediately before that synchronous compositor call. Those are
the same view scales used by Clutter's capture-size calculation. Their exact
maximum bounds the allocation and is frozen into the topology signature; the
returned scale and `round(stage size × returned scale)` texture dimensions
must then match it. No portal, private D-Bus property or monitor picker is
queried to infer the layout mode. This is one uniformly scaled compositor
stage, not a stitch of per-output native pixel buffers.
`Shell.Screenshot.composite_to_stream()` reads the selected texture pixels
back to CPU memory synchronously before its asynchronous PNG save completes,
so Enter can pause Shell briefly in proportion to the selected area. The
32-million-pixel preflight cap bounds that work, but a future zero-copy or
worker-owned compositor export would be preferable. The first-paint selection
surface remains PNG-free and is not affected by this readback. Mutter's
supported fractional monitor scales are recovered as their canonical
denominator-at-most-four fraction before crop arithmetic. Both crop edges and
the protocol use that same fraction, preventing Clutter's float32 scale from
adding a phantom edge pixel at origins that should land exactly on the pixel
grid.

## Validate

From this directory:

```sh
npm test
python3 ../../tests/gjs_shell_bridge_handoff_test.py
for module in snipsnap-shell-bridge/*.js; do
  node --check --input-type=module < "$module"
done
xmllint --noout \
  snipsnap-shell-bridge/schemas/*.xml
glib-compile-schemas --strict --dry-run \
  snipsnap-shell-bridge/schemas
```

Repository packaging owns creation and validation of the installable extension
archive. The source tree intentionally contains only schema XML; GNOME's
installer compiles it at installation time.

The schema's safe development binding is `Super+Shift+S`. The activation tool
may move the bridge to `Print`. Before it does, it snapshots and retires any
custom media-key binding at
`/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snipsnap/`
that launches `snipsnap gui`, and removes bare `Print` from GNOME's own
`org.gnome.shell.keybindings` screenshot keys. Both changes are explicit and
reversible; the extension never modifies or monkey-patches GNOME's built-in
screenshot implementation.

The user activation and exact rollback procedure is documented in
`docs/gnome-shell-bridge.md`. Its tool requires an owner-only binding
snapshot, an exact manifest-attested installed tree, a digest-bound
fresh-Shell load for every new payload, and an enabled **and ACTIVE**
extension before moving `Print`.

## GNOME 50/51 smoke test

From the repository root, build an isolated bundle with the repository-owned
packager. Install it only into a disposable GNOME user session, enable the
UUID, and follow logs in the user journal:

```sh
python3 packaging/gnome-shell/package-extension.py \
  contrib/gnome-shell-extension/snipsnap-shell-bridge \
  /tmp/snipsnap-shell-bridge.shell-extension.zip
gnome-extensions install --force \
  /tmp/snipsnap-shell-bridge.shell-extension.zip
# The packager refuses to archive schemas/gschemas.compiled, and
# `gnome-extensions install` only unpacks, so compile the schema in place before
# enabling. Without this, Extension.getSettings() throws on load. The supported
# install path (snipsnap-shell-bridge activate) does this for you; this manual
# smoke test has to do it itself.
glib-compile-schemas --strict \
  ~/.local/share/gnome-shell/extensions/snipsnap-shell-bridge@kleos.norvi.tech/schemas
gnome-extensions enable snipsnap-shell-bridge@kleos.norvi.tech
journalctl --user -f -o cat | grep '\[snipsnap-shell-bridge\]'
```

The extension metadata allows only the `user` session mode. The runtime also
rejects capture unless the current mode is `user` or inherits from `user`, has
windows, and is neither locked nor the greeter. A lock transition or monitor
topology change invalidates an in-flight capture and destroys any retained
stage texture.

## GNOME 51 compatibility

Metadata follows GNOME's major-only convention and declares `50` and `51`.
`tests/validate_gnome_shell_51_source.py` checks out exact upstream GNOME Shell
and Mutter 51 commits and verifies every Shell and Mutter API the bridge uses:
the compositor screenshot and selected-texture encoder, modal and screenshot
groups, keybindings, session guards, raw captured events, stage-view scales,
deferred focus activation and MetaWindow identity methods. That source gate
complements the real GNOME 50 compositor end-to-end test below; a GNOME 51
nested or physical-session run is still required before treating 51 latency
and mixed-monitor behaviour as measured evidence.

## Compositor-to-editor end-to-end test

`tests/run_gnome_shell_bridge_e2e.py` runs `tests/gnome_shell_bridge_e2e.js`
inside GNOME Shell's own test runner on a real headless Mutter Wayland
compositor, against a daemon built with `-DENABLE_GNOME_SHELL_BRIDGE=ON`
(`scripts/demo-rig/Dockerfile` is a container that has everything it needs).
The script adds three virtual 1280x720 monitors, injects the actual `Print`
key, asserts the non-overlapping 3840x720 topology, drags across every output,
commits the selected pixels, and requires the exact authenticated native Qt
editor to be mapped, showing and focused.

The paired log validator binds the selection geometry to the Shell encoder and
the native receiver's decoded request, requires the complete ordered
lifecycle, requires the same immutable topology ID from capture through
encoding, checks the exact monitor layout and scale-1 stage/texture contract,
and fails on every bridge refusal or failure, JavaScript error, compositor
critical, or gross latency regression. Its latency limits are single-sample
headless sanity ceilings, not performance acceptance for a physical desktop.
The run keeps the frozen-overlay and editor images, structured log, evidence
summary, package manifest, checksums and deterministic extension bundle for
inspection.

A second session (`tests/gnome_shell_bridge_mixed_e2e.js`) covers both
DisplayConfig layout modes. It first applies a physical layout with three
100 % outputs and one 200 % rotated output, requires an exact scale-1 texture
and selected PNG with a landmark on every output and a stage hole, then
switches those outputs to logical mode at 100 %, 125 %, 150 % and 200 % with
two rotations and requires an exact scale-2 PNG with the same landmarks. Only
after both compositor proofs pass does it inject `Print`, drag across all
outputs, and complete the authenticated editor handoff. The editor proof
requires an exact selection-sized canvas, finds all four landmarks and the
stage hole in the Fit view, then proves 1:1, a real two-axis pan and Fit
restoration. This mixed run is a pixel and topology correctness gate, not a
latency benchmark.

Pure topology tests independently cover logical, physical and ambiguous
all-scale-1 layouts, including mirrored and tiled outputs with multiple CRTC
StageViews per logical monitor. They require every logical monitor's scale
multiplicity to be represented while allowing additional matching views, bind
the complete view list and mode changes into topology equality, budget the
exact maximum StageView allocation, and reject a stale returned scale or
texture.

The end-to-end test intentionally creates another window first and CI runs
it with `G_DEBUG=fatal-criticals`, which reproduces stack-order races hidden
by an empty desktop. It validates the compositor, input, handoff and editor path; a
headless software renderer cannot establish no-flash, no-sound or GPU latency
behaviour on a physical desktop, which is measured separately.
