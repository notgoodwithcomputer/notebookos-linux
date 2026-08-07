#!/usr/bin/env python3
"""The Schedule view must show the whole week without scrolling.

WHAT THIS EXISTS FOR. The timetable was drawn at a single fixed density of 1.05
pixels per minute, so the grid demanded (last minute - first minute) * 1.05
pixels no matter how much room it actually had. On the smallest panel this OS
supports the schedule pane's viewport is 623px, and an ordinary 08:00-18:00
week wanted 710 — the last hour of every weekday sat 87px BELOW THE FOLD. A
single evening class put it 213px down; a 07:30 lab and a 21:00 seminar 339px.
The one thing the view promises is the week at a glance, and it could not
deliver that on any screen this OS supports.

The check is deliberately written against the DRAWN GEOMETRY — `_blocks` and
`_grid_ppm`/`_grid_bottom`, all three recorded BY _draw_timetable as it paints —
rather than against the density constant or anything this file recomputes. A
check that asserted `_MIN_PX <= 1.0` would pass a rewrite that fitted the grid
by clipping the last hour off the model instead of by compressing it, which is
the same bug wearing a different number.

TWO WAYS THIS SUITE WAS ITSELF TOO WEAK, both caught by running it against the
broken build rather than by reading it:

  * it first measured only the bottom of the last CLASS BLOCK. A week whose last
    class ends at 16:50 keeps every block above the fold while the 17:00 and
    18:00 rows are still cut off, so the reader cannot see the end of their own
    day or click the empty evening to add to it. That is the bug, and the suite
    called it a pass.
  * it then computed the grid's bottom by calling _px_per_min() itself. That is
    not a measurement: with the fix reverted the app painted an 823px grid while
    this suite calmly reported 573px and passed. A gate that re-derives what the
    code should have done cannot see the code not doing it.

RED PROOF (M1 — a gate nobody has watched fail is decoration). Reverting the
fix by pinning the density back to its old fixed value:

    -        ppm = self._px_per_min(area.get_allocated_height(), lo, hi)
    +        ppm = self._MAX_PX

makes this suite say, measured:

    FAIL the ordinary week fits the pane
         <- the day runs 37px below the fold (grid 660px, pane 623px)
    FAIL an evening class still fits
         <- the day runs 163px below the fold (grid 786px, pane 623px)
    FAIL a 07:30 lab and a 21:00 seminar still fit
         <- the day runs 289px below the fold (grid 912px, pane 623px)
    FAIL the week still fits a 1366x740 laptop
         <- the day runs 19px below the fold
    RESULT: 4 FAILED
"""
import os
import sys
import json
import shutil

# Per-PROCESS home: a fixed one is shared by two copies of this suite, and the
# loser of nbapp's single-instance race is os._exit(0)ed with no output, which
# reads as a pass while nothing was tested.
H = "/tmp/nbhome-acadfit-%d" % os.getpid()
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

# The real budget: 1024x768 minus shell.py's 46px panel strut.
# docs/PAPER-PHYSICS.md sec E3.6.
BUDGET_W, BUDGET_H = 1024, 722

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


def week(meets):
    """A term whose classes meet at `meets` — one class per meeting."""
    return {"classes": [{"label": "Class %d" % i, "color": "#9A7B4F",
                         "room": "Room %d" % i, "instructor": "",
                         "meets": [m]} for i, m in enumerate(meets)],
            "lectures": [], "homework": [], "active": -1}


def draw_week(meets, w=BUDGET_W, h=BUDGET_H):
    """Build the app at `w`x`h`, show the Schedule, and really paint it.

    Returns (blocks, pane height, px-per-minute). The blocks are what the draw
    handler actually painted, so this measures the picture, not the intent."""
    with open(H + "/.config/notebook/academics.json", "w") as f:
        json.dump(week(meets), f)
    win = academics.Academics()
    win.set_default_size(w, h)
    win.resize(w, h)
    win._set_view("schedule")
    pump()
    # Render offscreen at exactly the budget so the grid gets its real
    # allocation. get_preferred_size() alone would only report the REQUEST,
    # and the request was never the thing that was wrong.
    off = Gtk.OffscreenWindow()
    child = win.get_child()
    win.remove(child)
    off.add(child)
    off.set_size_request(w, h)
    off.show_all()
    pump()
    off.get_pixbuf()                     # forces the draw handler to run
    pump()
    pane = win.grid_area.get_parent().get_allocation().height
    blocks = list(win._blocks)
    # Everything below is READ BACK FROM THE PAINT (_grid_ppm / _grid_bottom are
    # recorded by _draw_timetable), never recomputed here. The first version of
    # this suite called _px_per_min() itself to work out where the grid ended,
    # which is not a measurement of anything — with the fix reverted it happily
    # reported a 573px grid while the app was painting an 823px one, and two of
    # the three fold checks passed on a build known to be broken.
    ppm = win._grid_ppm
    # The bottom of the DRAWN GRID, not of the last class block. Measuring the
    # blocks alone is too weak and was measured being too weak: with the fix
    # reverted, a week whose last class ends at 16:50 kept every block above the
    # fold while the 17:00 and 18:00 rows were still cut off — so the reader
    # could not see the end of their own day, or click the empty evening to add
    # anything to it, and this suite called that a pass.
    grid_bottom = win._grid_bottom
    return win, blocks, pane, ppm, grid_bottom


def bottom(blocks):
    return max((y + bh for _x, y, _w, bh, _ci, _m in blocks), default=0)


uishot.load_theme()

# ---------------------------------------------------------------- the fold
CASES = [
    ("the ordinary week fits the pane",
     [{"day": 0, "start": "09:00", "end": "10:20", "room": ""},
      {"day": 2, "start": "11:00", "end": "12:20", "room": ""},
      {"day": 4, "start": "14:00", "end": "16:50", "room": ""}]),
    ("an evening class still fits",
     [{"day": 0, "start": "08:00", "end": "09:20", "room": ""},
      {"day": 2, "start": "18:00", "end": "19:30", "room": ""}]),
    ("a 07:30 lab and a 21:00 seminar still fit",
     [{"day": 1, "start": "07:30", "end": "09:00", "room": ""},
      {"day": 3, "start": "19:30", "end": "21:00", "room": ""}]),
]

for name, meets in CASES:
    win, blocks, pane, ppm, grid_bottom = draw_week(meets)
    low = bottom(blocks)
    check(name, blocks and grid_bottom <= pane and low <= pane,
          "no blocks were drawn at all" if not blocks else
          "the day runs %dpx below the fold (grid %dpx, pane %dpx)"
          % (grid_bottom - pane, grid_bottom, pane))
    win.destroy()
    pump()

# ------------------------------------------------------- still legible with it
# Fitting by squashing the day into an unreadable smear would pass the check
# above and fail the reader, so the compressed grid has to stay legible: an
# hour row tall enough to hold its own label, and a normal 80-minute class
# still tall enough (>= 34px) for _draw_timetable to draw its time and room.
win, blocks, pane, ppm, _gb = draw_week(CASES[2][1])
hour_row = 60 * ppm
check("a compressed hour row still holds an 11px time label", hour_row >= 24,
      "an hour is only %.1fpx tall" % hour_row)
win.destroy()
pump()

win, blocks, pane, ppm, _gb = draw_week(
    [{"day": 0, "start": "09:00", "end": "10:20", "room": "Lab 3"}])
tall = [bh for _x, _y, _w, bh, _ci, _m in blocks]
check("an 80-minute class stays tall enough to show its time and room",
      tall and min(tall) >= 34,
      "tallest block is %s px" % (tall,))
win.destroy()
pump()

# ------------------------------------------------------------- not smeared out
# The other failure mode: a term with one short class, stretched over the whole
# pane so a 50-minute seminar becomes a 500px slab. The density has a ceiling
# for this, and it is the density the grid was originally tuned at.
win, blocks, pane, ppm, _gb = draw_week(
    [{"day": 0, "start": "10:00", "end": "10:50", "room": ""}])
check("a nearly empty week is not smeared over the whole pane",
      ppm <= academics.Academics._MAX_PX + 1e-6,
      "drawn at %.2f px/min, above the %.2f ceiling"
      % (ppm, academics.Academics._MAX_PX))
tall = [bh for _x, _y, _w, bh, _ci, _m in blocks]
check("a 50-minute class is still drawn as 50 minutes",
      tall and tall[0] <= 50 * academics.Academics._MAX_PX + 1,
      "a 50-minute class was drawn %s px tall" % (tall,))
win.destroy()
pump()

# ---------------------------------------------------------- a genuinely huge day
# 06:00-23:00 is past what any pane can show at a legible density. The right
# behaviour is to scroll, NOT to compress below the floor: an unreadable grid
# that happens to fit is not a fix.
win, blocks, pane, ppm, _gb = draw_week(
    [{"day": 0, "start": "06:00", "end": "07:00", "room": ""},
     {"day": 4, "start": "22:00", "end": "23:00", "room": ""}])
check("a 17-hour day scrolls rather than compressing past legibility",
      ppm >= academics.Academics._MIN_PX - 1e-6,
      "drawn at %.2f px/min, below the %.2f floor"
      % (ppm, academics.Academics._MIN_PX))
win.destroy()
pump()

# --------------------------------------------------------------- a bigger panel
# The fix must not have traded the small panel for the common laptop.
win, blocks, pane, ppm, grid_bottom = draw_week(CASES[0][1], w=1366, h=740)
check("the week still fits a 1366x740 laptop",
      blocks and grid_bottom <= pane and bottom(blocks) <= pane,
      "the day runs %dpx below the fold" % (grid_bottom - pane))
win.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
