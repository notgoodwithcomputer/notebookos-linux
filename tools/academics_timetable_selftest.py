#!/usr/bin/env python3
"""The timetable's pointer model: what a click on the drawn week means.

The grid is a cairo drawing, so GTK knows nothing about what is under the
pointer — every gesture is arithmetic against the geometry the draw handler
recorded, and arithmetic against geometry is exactly the kind of code that is
"obviously right" and off by a column.

Two behaviours are pinned here:

  * a DOUBLE-click on an empty slot adds a class time there, with the day and
    the hour filled in from where the pointer actually was. Before this, the
    only way to put a class on the timetable was a button at the top of the
    pane whose dialog always opened on Monday 09:00, whatever part of the week
    you were looking at and had just pointed to.
  * a SINGLE click does NOT. It focuses the grid for keyboard use and drops the
    selection. A modal that opens because somebody clicked the background is a
    trap, and this check is what stops the gesture being "simplified" into one.

RED PROOFS (M1), measured:

  1. make the gesture fire on a single click too
     (`if ev.type == Gdk.EventType._2BUTTON_PRESS:` -> `if True:`)
       FAIL a single click does not open anything
            <- clicking empty space opened the class-time dialog
  2. drop the prefill (`self._add_meeting(day=day, start=start)`
     -> `self._add_meeting()`)
       FAIL the dialog opens on the day that was clicked
            <- opened on day None, pointed at day 3
       FAIL the dialog opens at the hour that was clicked
            <- opened at None, pointed at 14:00
       FAIL the dialog's end is an hour after that start   <- None
  3. stop snapping to the half hour (`int(mins // 30) * 30` -> `int(mins)`)
       FAIL a click between the lines is snapped to the half hour
            <- (3, '14:17')
       FAIL a click past the half hour snaps back, not forward
            <- (2, '11:44')
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acadgrid-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk                            # noqa: E402
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


TERM = {
    "classes": [{"label": "Organic Chemistry", "color": "#9A7B4F",
                 "room": "D2210", "instructor": "",
                 "meets": [{"day": 0, "start": "09:00", "end": "10:20",
                            "room": ""}]}],
    "lectures": [], "homework": [], "active": -1}

with open(H + "/.config/notebook/academics.json", "w") as f:
    json.dump(TERM, f)

uishot.load_theme()
W, HGT = 1024, 722

app = academics.Academics()
app.set_default_size(W, HGT)
app.resize(W, HGT)
app._set_view("schedule")
pump()
off = Gtk.OffscreenWindow()
child = app.get_child()
app.remove(child)
off.add(child)
off.set_size_request(W, HGT)
off.show_all()
pump()
off.get_pixbuf()          # runs the draw handler, which records the geometry
pump()

lo, hi, ndays, col_w = app._grid_geom
ppm = app._grid_ppm


def point(day, mins):
    """The pixel at the middle of `day`'s column, at `mins` past midnight —
    the inverse of _slot_at, so the test drives real coordinates."""
    x = academics.Academics._GUTTER_W + day * col_w + col_w / 2.0
    y = academics.Academics._HDR_H + (mins - lo) * ppm
    return x, y


# ------------------------------------------------------------ the arithmetic
check("a point in a day column maps to that day and time",
      app._slot_at(*point(3, 14 * 60)) == (3, "14:00"),
      app._slot_at(*point(3, 14 * 60)))
check("a click between the lines is snapped to the half hour",
      app._slot_at(*point(3, 14 * 60 + 17)) == (3, "14:00"),
      app._slot_at(*point(3, 14 * 60 + 17)))
check("a click past the half hour snaps back, not forward",
      app._slot_at(*point(2, 11 * 60 + 44)) == (2, "11:30"),
      app._slot_at(*point(2, 11 * 60 + 44)))
check("the hour gutter is not a slot",
      app._slot_at(4.0, academics.Academics._HDR_H + 40) is None,
      app._slot_at(4.0, academics.Academics._HDR_H + 40))
check("the day header is not a slot",
      app._slot_at(*(point(2, lo)[0], 4.0)) is None,
      app._slot_at(*(point(2, lo)[0], 4.0)))
check("a point past the last day column is not a slot",
      app._slot_at(academics.Academics._GUTTER_W + ndays * col_w + 20,
                   academics.Academics._HDR_H + 40) is None)
# The dialog defaults an hour onto the start, so a slot in the last hour of the
# grid must not offer a start the meeting cannot finish after.
check("a slot in the last hour of the day is pulled back to leave room",
      app._slot_at(*point(1, hi - 10))[1] == academics._hhmm(hi - 60),
      app._slot_at(*point(1, hi - 10)))

# -------------------------------------------------------------- the gesture
opened = []
app._meeting_dialog = lambda *a, **k: opened.append(k) or None


def click(day, mins, double):
    ev = Gdk.EventButton()
    ev.type = (Gdk.EventType._2BUTTON_PRESS if double
               else Gdk.EventType.BUTTON_PRESS)
    ev.x, ev.y = point(day, mins)
    ev.button = 1
    return app._on_timetable_press(app.grid_area, ev)


del opened[:]
click(3, 14 * 60, double=False)
check("a single click does not open anything", not opened,
      "clicking empty space opened the class-time dialog")

del opened[:]
click(3, 14 * 60, double=True)
check("a double-click on an empty slot opens the class-time dialog",
      len(opened) == 1, opened)
if opened:
    check("the dialog opens on the day that was clicked",
          opened[0].get("day") == 3,
          "opened on day %r, pointed at day 3" % (opened[0].get("day"),))
    check("the dialog opens at the hour that was clicked",
          opened[0].get("start") == "14:00",
          "opened at %r, pointed at 14:00" % (opened[0].get("start"),))
    check("the dialog's end is an hour after that start",
          opened[0].get("end") == "15:00", opened[0].get("end"))

# A double-click ON A CLASS must still edit that class, not add a second one on
# top of it. The block hit-test runs first and returns before the gesture.
edited = []
app._edit_meeting = lambda ci, m: edited.append((ci, m["start"]))
del opened[:]
bx, by, bw, bh, _ci, _m = app._blocks[0]
ev = Gdk.EventButton()
ev.type = Gdk.EventType._2BUTTON_PRESS
ev.x, ev.y = bx + bw / 2.0, by + bh / 2.0
ev.button = 1
app._on_timetable_press(app.grid_area, ev)
check("a double-click on a class edits it rather than adding another",
      edited and not opened, "edited=%r opened=%r" % (edited, opened))

off.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
