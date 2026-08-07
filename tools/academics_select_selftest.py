#!/usr/bin/env python3
"""Selecting a lecture must not rebuild the notebook.

Clicking a lecture in the sidebar called `_refresh_sidebar`, which destroys and
reconstructs every class header and every lecture row there is — to change which
row carries one CSS class. On a term of 24 classes and 600 lectures (a four-year
degree kept in one file, which is exactly what an app that never asks you to
start a new notebook invites) that measured 375ms of widget construction ON
EVERY CLICK. Clicking through a list of notes should not cost a third of a
second a note.

`_set_active_row` now moves the highlight in place and falls back to the full
rebuild only when the row is not on screen — another view, or filtered out by a
search — which is the case that genuinely needs one.

MEASURED AS A RATIO, NOT A DURATION. This repository's checks run on a machine
that may be building an ISO, encoding a map or running two hundred other suites
at the same time; an absolute millisecond threshold on that machine is a
coin-toss that will eventually fail for reasons having nothing to do with this
code. Both operations are timed in the SAME process, back to back, and the
assertion is that selecting is a small fraction of rebuilding. That ratio holds
whatever else the machine is doing.

RED PROOF (M1), measured. Putting the rebuild back in _select --
    -   if not self._set_active_row(i):
    -       self.active = i
    -       self._refresh_sidebar()
    +   self.active = i
    +   self._refresh_sidebar()
gives:

    FAIL selecting is far cheaper than rebuilding the sidebar
         <- select 575.5ms vs rebuild 413.8ms (1.39x, needs to be under 0.34x)
    10 checks, 1 failed

Exactly ONE check fails, and the nine correctness checks STAY GREEN — which is
the point of
having both: the old code was correct and slow, so only a cost check can tell
the two apart. Getting that right needed care -- the first version of this file
captured `app._lec_rows` once, and since the rebuild path replaces those widgets
the correctness checks went red too, for a reason having nothing to do with
correctness.
"""
import os
import sys
import json
import shutil
import time

H = "/tmp/nbhome-acadsel-%d" % os.getpid()
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


def pump(n=600):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


NC, NL = 24, 600
term = {
    "classes": [{"label": "Course %02d" % i,
                 "color": academics.CLASS_COLORS[i % 5], "room": "R%d" % i,
                 "instructor": "", "meets": []} for i in range(NC)],
    "lectures": [{"cls": i % NC, "num": "%02d" % (i // NC + 1),
                  "title": "Lecture %d" % i, "date": "2026-08-03", "meta": "",
                  "notes": "paragraph %d. " % i, "ranges": {}}
                 for i in range(NL)],
    "homework": [], "active": 0}
with open(H + "/.config/notebook/academics.json", "w") as f:
    json.dump(term, f)

uishot.load_theme()
app = academics.Academics()
app.set_default_size(1024, 722)
app.resize(1024, 722)
pump()
off = Gtk.OffscreenWindow()
child = app.get_child()
app.remove(child)
off.add(child)
off.set_size_request(1024, 722)
off.show_all()
pump()
off.get_pixbuf()
pump()

# ----------------------------------------------------------- it still works
# ALWAYS re-read app._lec_rows. Holding the dict from before a call would test
# which widgets EXIST rather than which row is marked: the fallback path rebuilds
# the sidebar, so its rows are new objects and a captured handle points at
# destroyed ones. That made these checks fail under the rebuild mutation for a
# reason that had nothing to do with correctness -- and correctness has to stay
# green there, so that ONLY the cost check tells the two implementations apart.
def klasses(index, part):
    return app._lec_rows[index][part].get_style_context().list_classes()


first, second = 0, NC + 5          # two lectures in different classes
app._select(first)
pump()
check("selecting sets the active lecture", app.active == first, app.active)
check("the selected row is marked active", "active" in klasses(first, 0),
      klasses(first, 0))

app._select(second)
pump()
check("selecting another moves the mark", app.active == second, app.active)
check("...onto the new row", "active" in klasses(second, 0),
      klasses(second, 0))
check("...and off the old one", "active" not in klasses(first, 0),
      klasses(first, 0))
check("the lecture number is marked too", "active" in klasses(second, 1),
      klasses(second, 1))
# _on_title_changed writes through this handle; if it lags the selection, typing
# a title renames the row you just left.
check("the live title handle followed the selection",
      app._active_title_label is app._lec_rows[second][2])

# The canvas has to have been rebuilt for the newly selected lecture, or the
# highlight moves while the page still shows the old note.
txt = app.body.get_buffer()
body = txt.get_text(txt.get_start_iter(), txt.get_end_iter(), False)
check("the note on screen is the one selected",
      body.strip() == ("paragraph %d." % second), repr(body[:40]))

# ------------------------------------------------- a row that is NOT on screen
# A search can filter the target out; then the full rebuild is correct and must
# still happen rather than the selection silently not moving.
app.search.set_text("paragraph 7.")
app._filter_tick()
pump()
app._select(11)
pump()
check("selecting a filtered-out lecture still selects it", app.active == 11,
      app.active)
app._clear_search()
pump()

# ------------------------------------------------------------------ the cost
app._select(first)
pump()


def timed(fn, n=6):
    fn()
    pump()
    t = time.monotonic()
    for _ in range(n):
        fn()
    pump()
    return (time.monotonic() - t) / n


rebuild = timed(app._refresh_sidebar)
flip = [second, first]
counter = [0]


def one_select():
    counter[0] += 1
    app._select(flip[counter[0] % 2])


select = timed(one_select)
ratio = select / rebuild if rebuild else 99
check("selecting is far cheaper than rebuilding the sidebar", ratio < 0.34,
      "select %.1fms vs rebuild %.1fms (%.2fx, needs to be under 0.34x)"
      % (select * 1000, rebuild * 1000, ratio))
print("     (select %.1fms, sidebar rebuild %.1fms, %d lectures)"
      % (select * 1000, rebuild * 1000, NL))

off.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
