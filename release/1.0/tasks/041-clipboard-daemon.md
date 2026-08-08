# Task 041 — clipboard persistence daemon

## Design

`xclipd.py` watches only the X11 `CLIPBOARD` selection through
`Gtk.Clipboard`'s `owner-change` signal.  A change to an external, live owner
causes a synchronous snapshot of the targets it can retain: text and image.
It never claims CLIPBOARD while that owner is live.  Only an ownerless event,
after a non-empty snapshot has been taken, makes the daemon publish the saved
value and call `store()`.  A pending-self-claim flag consumes the resulting
owner-change echo without taking another snapshot.  PRIMARY is never opened.

Text is capped at 1 MiB of UTF-8, truncating only on a valid codepoint boundary.
That is generous for ordinary prose while preventing an accidental giant copy
from pinning unbounded memory.  Images are capped at 16 MiB by decoded pixel
size (width × height × at least four channels), because a compact encoded image
can expand dramatically in memory.  Oversize images are not retained.

The GTK-free `ClipboardCore` expresses the ownership decisions for headless
testing.  A nonblocking `fcntl.flock` held for the process lifetime enforces
one daemon per machine, and SIGTERM exits the GTK main loop cleanly.

## Campaign session launch line

Place this beside the `nbmediakeys.py` launch.  The comment uses `session.sh`'s
reason-first voice and calls out the X11 lifetime problem the process solves:

```sh
# clipboard keeper: X11 selections belong to the copying window, so remember
# CLIPBOARD and serve it only after that window exits. PRIMARY stays untouched.
python3 /opt/notebook/de/xclipd.py >/dev/null 2>&1 &
```

## Verified suite output

Final run after both sabotage rounds were reverted (`python3
tools/xclipd_selftest.py`, exit 0):

```text
== HEADLESS ==
PASS: external copy requests a snapshot
PASS: dead owner makes daemon serve its snapshot
PASS: the daemon's ownership echo is ignored
PASS: a new legitimate live owner is only snapshotted
PASS: a snapshot event never asserts over that live owner
PASS: oversize text is UTF-8 capped
PASS: oversize decoded images are rejected
PASS: images at the decoded cap are accepted
PASS: a held lock refuses a second instance

== DISPLAY ==
SKIP: no display available

RESULT: ALL PASS
```

The display section is fully implemented, including daemon-not-running text
control, daemon-backed text persistence, daemon-backed image persistence, and
daemon teardown.  This sandbox has no X display, so GTK's real connection probe
produced the honest skip above; no fake display was used.

## Red-proof evidence

### Round 1 — break the self-echo guard

Sabotage: changed the `self-owner-change` decision from `IGNORE` to
`TAKE_SNAPSHOT`.  The real suite exited 1 with:

```text
== HEADLESS ==
PASS: external copy requests a snapshot
PASS: dead owner makes daemon serve its snapshot
FAIL: the daemon's ownership echo is ignored
PASS: a new legitimate live owner is only snapshotted
PASS: a snapshot event never asserts over that live owner
PASS: oversize text is UTF-8 capped
PASS: oversize decoded images are rejected
PASS: images at the decoded cap are accepted
PASS: a held lock refuses a second instance

== DISPLAY ==
SKIP: no display available

RESULT: FAILED
```

The sabotage was reverted.  The immediate rerun exited 0 with every headless
check passing and `RESULT: ALL PASS`.

### Round 2 — assert while a legitimate owner is live

Sabotage: changed the `snapshot-taken` decision from `IGNORE` to
`ASSERT_OWNERSHIP`.  The real suite exited 1 with:

```text
== HEADLESS ==
PASS: external copy requests a snapshot
PASS: dead owner makes daemon serve its snapshot
PASS: the daemon's ownership echo is ignored
PASS: a new legitimate live owner is only snapshotted
FAIL: a snapshot event never asserts over that live owner
PASS: oversize text is UTF-8 capped
PASS: oversize decoded images are rejected
PASS: images at the decoded cap are accepted
PASS: a held lock refuses a second instance

== DISPLAY ==
SKIP: no display available

RESULT: FAILED
```

The sabotage was reverted.  The immediate rerun exited 0 with every headless
check passing and `RESULT: ALL PASS`.  A final `py_compile` plus suite run also
exited 0 and produced the green output recorded above.

## Follow-up

A desktop clipboard manager confounded the display control: clipboard data
survived without xclipd, making both that control and the positive persistence
checks unable to distinguish xclipd's behavior.  The display section now
snapshots the current text, publishes a unique marker from a child that exits,
reads it back with xclipd stopped, and restores the prior text best-effort.  If
the marker survives, the whole display section skips without failure.  The full
display proof therefore runs only where it can mean something: bare X, such as
the guest matchbox session or a clean Xvfb.
