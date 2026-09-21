# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
**[Report a vulnerability](https://github.com/spencercnorton/snipsnap/security/advisories/new)**.
Do not open a public issue, and do not include real credentials, screenshots
of your own desktop or personal paths in the report — a description and a
minimal reproduction are enough.

There is no e-mail address for security reports; the advisory form is the
only channel, and it is the one that is monitored. You will get an
acknowledgement within a week. Fixes ship as a tagged release; the advisory
is published once the release is out, and credits you unless you ask
otherwise.

## Supported versions

Only the latest tagged release is supported. SnipSnap has no LTS line.

## Scope

In scope: this repository's code and the artefacts it ships — the `snipsnap`
binary, the GNOME Shell bridge extension and the Debian package.
Out of scope: GNOME Shell and Mutter themselves, the desktop-portal
implementations SnipSnap falls back to on other compositors, an external
annotator you have pointed it at, and deployments the maintainer does not
operate.

## What SnipSnap does with credentials and data

SnipSnap holds no credentials. Understanding the trust model helps you judge
what is and is not a finding:

- **The bridge socket is the security boundary.** The GNOME Shell extension
  hands a capture to the daemon over a Unix socket at
  `$XDG_RUNTIME_DIR/snipsnap/gnome-shell-bridge-v1.sock`. The daemon creates
  that directory 0700 and the socket 0600, and refuses to listen if either
  is owned by another user or wider than that. Each side checks the other
  with kernel-supplied credentials (`SO_PEERCRED`), never with anything the
  peer asserts: the daemon accepts a connection only from a process with its
  own UID whose PID currently owns `org.gnome.Shell` on the session bus, and
  the extension accepts only a peer with its own UID whose PID owns the
  `tech.norvi.snipsnap` bus name. The handshake is one request frame and
  three acknowledgements — request accepted, editor ready, commit accepted —
  each carrying the daemon's PID, which must match the socket peer; an
  unknown status, a malformed frame or a PID mismatch aborts the capture.
  One capture at a time: a second connection gets a busy reply and is
  closed. A finding that defeats any of those checks, or that lets a process
  obtain a capture it did not initiate, is the highest-severity class here.
- **Nothing leaves the machine.** The image-host uploader and the update
  checker are compiled out (`ENABLE_IMGUR=OFF`, `DISABLE_UPDATE_CHECKER=ON`),
  the release-feed URL in `CMakeLists.txt` is deliberately empty so nothing
  can quietly start polling again, and the package declares no TLS
  dependency. In the shipped build no code path opens a network connection;
  the only things SnipSnap talks to are the session D-Bus and its own Unix
  socket. Updates arrive through `apt`.
- **A capture goes only where you send it.** The bridge freezes the
  compositor stage in memory, encodes only the region you selected, and
  hands it to the daemon over the socket above without writing an image to
  disk. From the editor the pixels go to the clipboard, to the file you
  save, or — if you enabled it with `snipsnap-annotator` — to the external
  annotator's standard input. SnipSnap keeps no copy.
- **The D-Bus interface is not an authorisation boundary.** The
  `tech.norvi.snipsnap` name on your session bus accepts capture and
  clipboard calls from any process in the same session, as any D-Bus
  desktop application does. A process that can reach your session bus can
  already run `snipsnap` itself, so that alone is not a finding.
- **Local state** lives under `~/.config/snipsnap/` (the `snipsnap.ini`
  settings file; on first run the previous name's configuration is copied
  across and left in place) and `~/.cache/snipsnap/` (0700; the last
  selection rectangle in `region.txt`, 0600). With `SNIPSNAP_CAPTURE_TRACE`
  set, a timing trace goes to stderr, or with the value `file` is appended
  to `~/.cache/snipsnap/capture-trace.jsonl` (0600); it records event names
  and durations, never pixels, paths, window titles or clipboard contents.
  No telemetry is sent anywhere.
