#!/usr/bin/env python3
"""Writer's Edit menu prints Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+A — drive the real
window and watch those chords do the thing.

accelerator_promise_check credits these four to GTK: a focused GtkTextView
carries Cut / Copy / Paste / Select All as class bindings, so Writer needs no
app handler for them and rightly has none.  That is a claim about the TOOLKIT,
and a static gate cannot check it — if a later change put a key handler on the
editor that swallowed Ctrl+X, the static gate would still call the menu honest
while the menu had started lying.  So this drives the app the way a person
does: real window at the panel size, real key ladder, real clipboard, and the
BUFFER as the witness.  Nothing here calls _clip() or _select_all() by name.

Each chord is given its OWN prepared document rather than inheriting the state
the previous chord left.  Any X client on the display can take the PRIMARY
selection, and a GtkTextBuffer drops its selection when that happens — chained
steps failed here about one run in three for a reason that had nothing to do
with Writer.  prepare() re-presses Ctrl+A (the chord, never select_range()) and
says so if the selection will not stay.

    tools/guestrun.sh python3 tools/writer_clipboard_selftest.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

HOME = "/tmp/nb-writer-clipboard-selftest"
shutil.rmtree(HOME, ignore_errors=True)

import appdrive                                                  # noqa: E402
from gi.repository import Gtk, Gdk                               # noqa: E402

SAMPLE = "Hello world"
failures = []


def check(name, ok, detail):
    print("%-38s %s   %s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        failures.append(name)
    return ok


def body_text(app):
    buf = app.buf
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def selected(app):
    buf = app.buf
    if not buf.get_has_selection():
        return ""
    start, end = buf.get_selection_bounds()
    return buf.get_text(start, end, False)


def prepare(app, drive, select=True):
    """A fresh document with SAMPLE typed in, selected with the real Ctrl+A."""
    app.buf.set_text("")
    app.body.grab_focus()
    drive.pump(0.2)
    drive.type(SAMPLE)
    drive.pump(0.2)
    if not select:
        return body_text(app) == SAMPLE
    for _attempt in range(3):
        drive.key("a", ctrl=True)
        drive.pump(0.2)
        if selected(app) == SAMPLE:
            return True
    return False


def main():
    drive = appdrive.Drive("writer", home=HOME)
    app = drive.app
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    # Ctrl+A — the document is typed, then selected by the chord alone.
    prepare(app, drive, select=False)
    check("typing reaches the document", body_text(app) == SAMPLE,
          "buffer is %r" % body_text(app))
    drive.key("a", ctrl=True)
    drive.pump(0.2)
    check("Ctrl+A selects the document", selected(app) == SAMPLE,
          "selection is %r" % selected(app))

    # Ctrl+C — the clipboard is poisoned first, so a stale value cannot pass.
    clipboard.set_text("<<nothing was copied>>", -1)
    drive.pump(0.2)
    ready = prepare(app, drive)
    drive.key("c", ctrl=True)
    drive.pump(0.4)
    copied = clipboard.wait_for_text()
    check("Ctrl+C copies the selection", ready and copied == SAMPLE,
          "clipboard is %r" % copied)

    # Ctrl+X — cut is copy AND delete; both halves are asserted.
    clipboard.set_text("<<nothing was cut>>", -1)
    drive.pump(0.2)
    ready = prepare(app, drive)
    drive.key("x", ctrl=True)
    drive.pump(0.4)
    cut_left = body_text(app)
    cut_took = clipboard.wait_for_text()
    check("Ctrl+X removes what it copied",
          ready and cut_left == "" and cut_took == SAMPLE,
          "buffer is %r, clipboard is %r" % (cut_left, cut_took))

    # Ctrl+V — into the document Ctrl+X just emptied.
    drive.key("v", ctrl=True)
    drive.pump(0.4)
    check("Ctrl+V puts it back", body_text(app) == SAMPLE,
          "buffer is %r" % body_text(app))

    # A green check nobody has seen go red is not evidence. Stage exactly the
    # regression this test exists for — a handler on the editor swallowing
    # Ctrl+X before GTK's own binding sees it — and watch the cut check fail
    # while typing and Ctrl+A around it still pass.
    def swallow(_widget, event):
        return bool(event.state & Gdk.ModifierType.CONTROL_MASK
                    and event.keyval in (Gdk.KEY_x, Gdk.KEY_X))

    handler = app.body.connect("key-press-event", swallow)
    ready = prepare(app, drive)
    drive.key("x", ctrl=True)
    drive.pump(0.4)
    mutant_left = body_text(app)
    app.body.disconnect(handler)
    check("MUTANT: a swallowed Ctrl+X fails the cut check",
          ready and mutant_left == SAMPLE,
          "typing and Ctrl+A still fine; buffer after Ctrl+X is %r" % mutant_left)

    drive.close()
    print("RESULT: " + ("ALL PASS" if not failures
                        else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
