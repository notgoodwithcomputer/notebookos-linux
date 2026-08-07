#!/usr/bin/env python3
"""The first screen of a fresh install must not repeat itself.

Academics ships empty — no classes, no lectures, no assignments — so the empty
state IS the first thing every user sees, on all three views. It was saying the
same words over and over:

  * Notes / Schedule / Homework sidebar: the heading said "No classes" and the
    line directly beneath it said "No classes". Verbatim, one line apart, on
    all three views. The code comment above that line even described what it
    was supposed to say instead ("explains what the pane is for rather than
    repeating it") — the implementation had regressed to the exact thing the
    comment said not to do.
  * Schedule: a third copy, in the subtitle under "Week", directly above an
    empty state whose title was also "No classes". Three identical labels on
    one screen.
  * Homework: "No assignments" in the subtitle and again in the empty state.

WHAT IS AND IS NOT CHECKED. Only NON-INTERACTIVE labels are collected. An action
offered twice — the "Add a class time" button in the header and again in the
empty state — is a deliberate and ordinary empty-state pattern, and failing it
would be this suite inventing a defect. Repeated STATUS TEXT is the bug: it
tells the reader nothing the second time and makes a sparse screen look broken.

RED PROOF (M1). This one did not need a synthetic mutation: it went red on its
first run, against a build where the duplicates had supposedly just been fixed,
and it was RIGHT — two of the three copies on the Schedule screen were gone and
the third had been missed, because the sidebar heading and the empty-state title
are in different panes and eyeballing the screenshot did not catch it:

    FAIL nothing is said twice on the empty Schedule screen
         <- 'No classes' appears 2x

That is the failure that earned this file. The remaining copy was the empty-state
title, which now always reads "No class times" — this pane's subject is when your
classes meet, and it has none of those whichever way an empty term is described.

Re-running with all three fixes reverted (the sidebar empty line, the Schedule
subtitle and the Schedule empty-state title all back to `_t("No classes")`)
fails three checks — and note the count on Schedule, which is FOUR copies of the
same two words on one screen, not the three I had counted by eye:

    FAIL nothing is said twice on the empty Notes screen
         <- 'No classes' appears 2x
    FAIL nothing is said twice on the empty Schedule screen
         <- 'No classes' appears 4x
    FAIL nothing is said twice on the empty Homework screen
         <- 'No classes' appears 2x

The two "names what IT will hold" checks stay green under that mutation, because
they read the _SIDEBAR_EMPTY table rather than the rendered label — they guard
the table against being flattened back to one shared string, which is a
different regression, and the duplicate-text check above is what guards the
screen. Worth knowing which check covers which: neither alone is enough.
"""
import os
import sys
import shutil

H = "/tmp/nbhome-academpty-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import academics                                              # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def texts(widget, in_button=False, out=None):
    """Every visible non-interactive label under `widget`.

    Labels inside a Gtk.Button are skipped: an action offered twice on an empty
    screen is a pattern, not a defect (see the module docstring)."""
    if out is None:
        out = []
    if not widget.get_visible():
        return out
    if isinstance(widget, Gtk.Label):
        if not in_button:
            t = (widget.get_text() or "").strip()
            if t:
                out.append(t)
        return out
    if isinstance(widget, Gtk.Container):
        inb = in_button or isinstance(widget, Gtk.Button)
        for c in widget.get_children():
            texts(c, inb, out)
    return out


uishot.load_theme()
W, HGT = 1024, 722

seen = {}
for view in ("notes", "schedule", "homework"):
    app = academics.Academics()
    app.set_default_size(W, HGT)
    app.resize(W, HGT)
    app._set_view(view)
    pump()
    off = Gtk.OffscreenWindow()
    child = app.get_child()
    app.remove(child)
    off.add(child)
    off.set_size_request(W, HGT)
    off.show_all()
    pump()
    off.get_pixbuf()
    pump()

    found = texts(child)
    dupes = sorted({t for t in found if found.count(t) > 1})
    check("nothing is said twice on the empty %s screen" % view.title(),
          not dupes,
          "; ".join("%r appears %dx" % (t, found.count(t)) for t in dupes))

    # And the sidebar's empty line must not restate the heading above it.
    heading = app.side_summary.get_text().strip()
    line = academics.Academics._SIDEBAR_EMPTY.get(view, "")
    check("the %s sidebar names what IT will hold" % view.title(),
          line and line.strip() != heading,
          "says %r, the same as the heading" % (line,))
    seen[view] = line
    off.destroy()
    pump()

# Each view describes its OWN pane, so the three lines must differ from each
# other too — one shared line would be the original defect wearing a new string.
check("each view's empty line is its own", len(set(seen.values())) == 3, seen)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
