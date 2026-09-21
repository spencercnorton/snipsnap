# How SnipSnap captures the screen on GNOME Wayland

SnipSnap is a Flameshot fork. Annotating, copying and saving are unchanged.
What it replaces is the capture itself on GNOME Wayland: a small GNOME Shell
extension freezes the compositor stage, draws the selection overlay across
every monitor, and hands only the selected pixels to the SnipSnap daemon over
a private socket. The code is in `src/core/`, `src/widgets/capture/` and
`contrib/gnome-shell-extension/`.

## The problem

On GNOME Wayland the standard route is the XDG Screenshot portal. It has no
client option to disable compositor effects: GNOME's portal backend asks Shell
for a flashing screenshot, so the screen flashes and the shutter sound plays
before anything is selected. The image is then PNG-encoded, written to disk,
read back and decoded before the tool can show its own interface.

The inherited capture path also takes one screen at a time. Its model — one
image, one screen index, one widget offset, one device-pixel ratio — cannot
describe a selection that starts on a monitor at 100 % and ends on a rotated
monitor at 200 %.

What SnipSnap wants instead:

- no flash, capture sound or per-capture monitor prompt;
- one immediately responsive selection surface covering every output;
- selections that may begin on any output and cross output boundaries;
- correct logical-to-pixel mapping for scale, transform, gaps and hotplug;
- no capture while the session is locked; and
- a permission-respecting fallback on other compositors.

## Two capture paths

The daemon selects a backend explicitly:

1. **GNOME Shell bridge.** Compiled in with the CMake option
   `ENABLE_GNOME_SHELL_BRIDGE` (the Debian package always sets it). When the
   daemon starts under `XDG_SESSION_TYPE=wayland` with `GNOME` in
   `XDG_CURRENT_DESKTOP`, it binds an owner-only Unix socket and waits for the
   extension; on any other session the receiver is never created.
2. **XDG Screenshot portal.** The compatibility fallback for every other
   Wayland compositor, with the portal's own permission prompt and privacy
   indicator.

A ScreenCast/PipeWire backend is the intended multi-output path for other
compositors. It is not implemented; they use the portal today.

The Shell component owns the shortcut, the frozen stage, the rectangle
selection, cancellation and the handoff — nothing else.

## The GNOME Shell bridge

### What the extension promises

- It runs only in the normal unlocked user session: the keybinding excludes
  the login, lock and unlock modes, and each trigger re-checks that the
  session is `user` (or inherits from it), unlocked, not the greeter, and has
  windows.
- It never monkey-patches GNOME's screenshot UI or D-Bus allow-lists, and
  exposes no D-Bus service, screenshot method, file access or network socket.
- It captures the stage *before* adding its overlay and shows the frozen stage
  as `ClutterContent` — no PNG, no file round trip before first paint.
- Shell monitor geometry is the authoritative logical coordinate space.
- The overlay stays modal until the daemon confirms the editor has painted and
  Shell has observed that exact window.
- Every actor, texture, grab, timer and signal handler is released on accept,
  cancel, disable, lock, monitor change or error.

### The capture sequence

```
user            GNOME Shell extension                    snipsnap daemon         editor
 |-- Print ---------->|                                        |                    |
 |                    | snapshot topology, check budget        |                    |
 |                    | screenshot_stage_to_content()          |                    |
 |                    |   (no portal, no PNG, no flash)        |                    |
 |<-- frozen overlay -| pushModal, dimmed stage                |                    |
 |-- drag, Enter ---->| composite_to_stream(selected rect)     |                    |
 |                    |-- connect $XDG_RUNTIME_DIR/snipsnap/gnome-shell-bridge-v1.sock
 |                    |   uid match; peer pid == owner of tech.norvi.snipsnap;      |
 |                    |   daemon: peer pid == owner of org.gnome.Shell              |
 |                    |-- 64-byte header + PNG --------------->|                    |
 |                    |<-- REQUEST_ACCEPTED -------------------|                    |
 |                    |                                        |-- decode, open --->|
 |                    |                                        |<-- first paint ----|
 |                    |<-- EDITOR_READY -----------------------|                    |
 |                    | observe window "SnipSnap [capture-id=N]" from that PID      |
 |                    |-- 16-byte COMMIT --------------------->|                    |
 |                    |<-- COMMIT_ACCEPTED --------------------|                    |
 |<-- overlay released|                                        |                    |
 |                    |-- activate once mapped (<= 8 frames, BEFORE_REDRAW) ------->|
```

**Trigger.** `Print` (or `Super+Shift+S` before activation) arrives through
Mutter's keybinding API. The extension refuses if a capture is already open or
a previous selection encode has not settled.

**Freeze.** The extension first snapshots the topology: stage size, every
logical monitor rectangle and scale, and the sorted scales of Clutter's stage
views. It rejects a projected texture above 32 million pixels or 32,768 pixels
on either axis, then calls `Shell.Screenshot.screenshot_stage_to_content()`,
which paints the whole stage into one texture. The topology is snapshotted
again afterwards; any change discards the capture. The returned scale must
equal the frozen maximum view scale and the texture must be exactly
`round(stage × scale)` in each dimension.

**Overlay.** One `St.Widget` bound to the stage holds the frozen content, four
shade rectangles dimming everything outside the selection, a selection frame,
a dimensions label and an instruction line. It is added to Shell's screenshot
UI group and made modal; the frame and labels follow the desktop accent colour
on GNOME 47 and later. Escape, a lock transition, a monitor change or an
extension disable all close it.

**Select.** Drags are clamped to the stage and kept in logical stage
coordinates, so a selection can span any number of outputs.

**Encode.** Enter re-checks the topology, then reads back and PNG-encodes only
the selected rectangle with `Shell.Screenshot.composite_to_stream()`, rounded
outward so a fractional-scale selection never loses an edge pixel. The
readback is synchronous inside Shell and cannot be cancelled once started, so
a module-lifetime gate refuses a new capture until it settles — even if Escape
or a lock has already removed the overlay.

**Handoff.** The extension connects to the daemon's socket. If nothing is
listening it asks the session bus to activate `tech.norvi.snipsnap` and retries
a bounded number of times; it never spawns a process itself. The daemon accepts
the request, decodes the PNG on Qt's thread pool, opens the editor directly and
reports first paint. Only when Shell has also seen a new normal window from
that exact PID, with SnipSnap's window identity and the exact title, does it
send the commit and release the overlay. It then activates the editor once the
window's compositor actor is mapped, retrying for at most eight frames in
Mutter's `BEFORE_REDRAW` phase — never synchronously, because activation racing
the window's stack insertion can trip a Mutter assertion.

## The handoff protocol

The socket is `$XDG_RUNTIME_DIR/snipsnap/gnome-shell-bridge-v1.sock`. The
daemon creates the directory `0700`, `chmod`s the socket to `0600` and verifies
ownership and mode before listening. A stale socket is removed only if the
user owns it and it refuses connections.

Three fixed-size frames, all big-endian:

| Frame | Size | Contents |
|---|---|---|
| Request | 64-byte header + PNG | magic `FSBRPNG1`, header size (u32, 64), flags (u32, 0), capture ID (u64), logical x/y (i32), logical width/height (u32), pixel width/height (u32), scale numerator/denominator (u32), payload length (u64), then the PNG bytes |
| ACK | 32 bytes | magic `FSBRACK1`, capture ID, status (u32), daemon PID (u32), reserved zero |
| Commit | 16 bytes | magic `FSBRCMT1`, capture ID |

Statuses are `REQUEST_ACCEPTED`, `EDITOR_READY`, `COMMIT_ACCEPTED`, and the
terminal errors `MALFORMED_REQUEST`, `BUSY`, `DECODE_FAILED`, `INTERNAL_ERROR`
and `TIMEOUT`. Both sides enforce the same bounds: dimensions at most 32,768,
at most 32 million pixels, PNG at most 128 MiB, scale between 1/2 and 4, and
pixel dimensions that agree with the logical rectangle and scale to within one
pixel. The daemon also requires a non-interlaced 8-bit static PNG with IHDR
first, rejects the animated-PNG chunks `acTL`, `fcTL` and `fdAT`, and caps
chunk count and ancillary metadata.

The daemon serves one request at a time; a second connection while a request,
decode or committed editor is outstanding receives `BUSY`, and the overlay
tells the user to close the open editor first. Daemon deadlines are 5 s for
the request, 3 s to decode and 3 s for the editor; the extension allows 6 s,
7 s, 3.5 s for the commit and 1.5 s to observe the window.

Authentication runs both ways. The extension accepts only a same-UID peer,
requires the kernel-reported peer PID to be the current D-Bus owner of
`tech.norvi.snipsnap`, and requires every ACK's daemon PID to match it. The
daemon reads `SO_PEERCRED` and requires the peer to be the current owner of
`org.gnome.Shell`. This stops cross-user access and accidental same-user
substitution; it is not a defence against a hostile process already running
as the desktop user, which needs an LSM-confined helper or a privileged broker.

A commit is conservatively irrevocable: from the first commit write no
client-side error can prove the daemon missed the frame, so a lost final ACK
is logged as `commit-ack-indeterminate`, the overlay is released and the
already-authenticated editor is exposed rather than duplicated. A disconnect
before commit closes the staged editor. Escape works throughout staging and
is refused only during the commit exchange. Earlier failures leave the frozen
overlay in place with the reason and the option to press Enter again.

## Presenting the editor on Wayland

An `xdg_toplevel` is not a virtual-desktop overlay: the compositor may bound
it to one output or work area, fullscreen and maximised roles are tied to one
output, and GNOME offers no desktop-wide layer-shell role. The editor cannot be
one surface spanning every monitor.

SnipSnap separates the **capture document** from its **presentation surface**:

- `CaptureWidget` remains an exact full-resolution canvas whose size equals the
  selected compositor rectangle; tools, annotations, undo state and export
  geometry stay in that coordinate system.
- `RegionEditorWindow` is one `QGraphicsView` sized to the component-wise
  smallest output. A `QGraphicsProxyWidget` embeds the whole `CaptureWidget`
  and maps paint, focus and pointer input through one scene transform.
- The initial scale fits the whole canvas and never enlarges it; black
  letterboxing makes gaps explicit. Fit (`Ctrl+0`), 1:1 (`Ctrl+1`), zoom,
  Ctrl+wheel around the cursor and middle-drag pan are unscaled host controls.
- Qt's Wayland platform cannot reliably surface the embedded toolbar through a
  proxy, so the annotation tools, colour picker, size controls, Copy and Save
  live in a native strip owned by the host window; `Tab` hides it.
- The viewport repaints dirty regions only.

## The capture data model

The GNOME fast path yields one **stage-composite region**, not per-output
buffers. In logical layout mode the texture scale is the maximum monitor scale
and lower-scale outputs are resampled into it; in physical layout mode every
stage view is scale 1. The extension reads `peek_stage_views()` and each view's
`get_scale()` immediately before the capture call, freezes their maximum into
the topology signature, and later requires the returned scale and texture size
to match.

The snapshot fails closed unless the stage views match one complete GNOME
model: all scale 1, or the configured monitor scales with at least one view
per logical monitor (mirrored and tiled outputs may add matching views). It
refuses overlapping, disconnected or out-of-stage monitors, a stage that does
not equal the monitor extents, more than 16 monitors or 64 views, and any
topology change between preflight and encode.

Mutter's fractional scales are rationals with a denominator of at most four.
The float the compositor returns is canonicalised to that fraction before
either crop edge is computed, and the same fraction travels in the request, so
a 4/3 scale cannot grow a phantom edge pixel through float32 rounding.

## Optional external annotator

When the extension's `external-annotator` setting and the daemon's
`bridgeUseExternalAnnotator` key are both on, the daemon opens no editor.
`EDITOR_READY` then means "decoded"; on commit the daemon launches `satty`
with the PNG on standard input, `wl-copy` as its copy command and a
timestamped output name in the save directory, and the extension commits
without waiting for a window. The activation tool turns both keys on together
when `satty` and `wl-copy` are installed; one side alone degrades or loses
captures, so do not set them by hand. `SNIPSNAP_BRIDGE_ANNOTATOR` overrides
the program.

## Tracing a capture

Both halves log structured events with a monotonic clock and a per-capture ID,
never image content, filenames or clipboard data. The extension writes JSON
lines prefixed `[snipsnap-shell-bridge]` to the user journal. The daemon's
trace is off by default; start the daemon in a terminal with
`SNIPSNAP_CAPTURE_TRACE=stderr snipsnap` and it prints the bridge capture's
trace (`… snipsnap gui` traces the portal path only), and
`SNIPSNAP_CAPTURE_TRACE=file` appends it to `capture-trace.jsonl` in the
private cache directory.
