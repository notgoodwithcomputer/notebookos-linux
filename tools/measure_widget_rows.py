#!/usr/bin/env python3
"""
measure_widget_rows — calibrate widgets.py's row-budget constants against what
the cards ACTUALLY render, under the shipped theme.

`Widgets._row_caps()` decides how many rows each desktop card may show by doing
arithmetic over a table of hand-written `_*_PX` constants. Nothing checks those
numbers against the real widgets, and they drift:

  * `_GRID_ROW_PX` said 45 for a 44px row — an UNDER-count, which hands out
    space the grid is really using and clips the bottom card off the screen;
  * the chrome constants together OVER-counted by ~80px, which is worse in the
    other direction: the column refused to grant rows it had ample room for
    (measured: 574px of content in a 682px column, with the Workout card
    trimmed to nothing).

So: build the real column, measure every part, and print the constants. Run it
after any change to the cards' padding, font sizes or structure, and paste the
results into widgets.py.

    DISPLAY=:0 PYTHONPATH=tools:<overlay>/opt/notebook/de \
        python3 tools/measure_widget_rows.py
"""
import json
import os
import sys
import tempfile
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                              # noqa: E402

import uishot                                              # noqa: E402


def pump():
    for _ in range(30):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def find(widget, cls, out=None):
    """Every widget carrying `cls`, depth-first."""
    out = [] if out is None else out
    if widget.get_style_context().has_class(cls):
        out.append(widget)
    if isinstance(widget, Gtk.Container):
        for kid in widget.get_children():
            find(kid, cls, out)
    return out


def height(widget, width=620):
    return widget.get_preferred_height_for_width(width)[0]


def seed(cfg, tasks, events, exercises):
    json.dump([{"text": "Task %d" % (i + 1), "done": False}
               for i in range(tasks)],
              open(os.path.join(cfg, "tasks.json"), "w"))
    today = time.strftime("%Y-%m-%d")
    json.dump([{"title": "Event %d" % (i + 1), "date": today,
                "time": "%02d:00" % (9 + i)} for i in range(events)],
              open(os.path.join(cfg, "calendar.json"), "w"))
    log = {today: {"e%d" % i: [10] for i in range(exercises)}}
    json.dump({"show_widget": True,
               "exercises": [{"id": "e%d" % i, "name": "Exercise %d" % (i + 1),
                              "sets": 3, "reps": 10} for i in range(exercises)],
               "log": log, "goals": {today: 3 * exercises}},
              open(os.path.join(cfg, "workout.json"), "w"))


def main():
    home = tempfile.mkdtemp(prefix="rowcal-")
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg)
    os.environ["NB_HOME"] = home
    uishot.load_theme()

    import nbapp
    import widgets

    # A tall panel so nothing is trimmed and every part renders at full size.
    nbapp.screen_size = lambda: (1920, 1080)
    W = 620

    # More tasks and events than any cap, so the "+N more" tail actually
    # renders — with a short list there is no tail to measure and the constant
    # silently reads as zero.
    seed(cfg, tasks=12, events=9, exercises=4)
    win = widgets.Widgets()
    col = win._col
    win._board.remove(col)
    off = Gtk.OffscreenWindow()
    off.set_size_request(W, 994)
    off.add(col)
    off.show_all()
    pump()

    def one(cls, parent=None):
        found = find(parent if parent is not None else col, cls)
        return max((height(w, W) for w in found), default=0)

    grid = find(col, "calgrid")[0]
    weeks = len(widgets._month_weeks(*time.localtime()[:2]))
    spacing = grid.get_row_spacing()
    grid_wd = one("calwd", grid) + spacing
    grid_row = max(height(c, W) for c in grid.get_children()
                   if isinstance(c, Gtk.EventBox)) + spacing
    grid_pad = height(grid, W) - grid_wd - weeks * grid_row

    # The Tasks card as a WHOLE, because the tile height is derived from it —
    # summing its parts misses the card border and the header rule.
    tasks_card = None
    for card in find(col, "card"):
        if find(card, "tasklist"):
            tasks_card = card
            break

    measured = {
        "_TASKS_CARD_PX": height(tasks_card, W) if tasks_card else 0,
        "_HEAD_PX": one("chead"),
        "_TASK_ROW_PX": one("taskrow"),
        "_MORE_ROW_PX": one("moretail"),
        "_AGENDA_ROW_PX": one("agrow"),
        "_AGSEC_PX": one("agsec"),
        "_GRID_WD_PX": grid_wd,
        "_GRID_ROW_PX": grid_row,
        "_GRID_PAD_PX": grid_pad,
        # The gap BETWEEN the two pinned cards — not BOARD_GAP, which is the
        # spacing between tiles. It went uncounted by the row budget for a
        # while and overflowed every panel below 1920 on a busy day, so it is
        # a checked constant now rather than an informational read-out.
        "_COL_SPACING_PX": col.get_spacing(),
    }

    # The empty-state lines only exist when the card has nothing in it, so they
    # need their own pass with empty stores.
    off.destroy()
    win.destroy()
    seed(cfg, tasks=0, events=0, exercises=0)
    win2 = widgets.Widgets()
    col2 = win2._col
    win2._board.remove(col2)
    off2 = Gtk.OffscreenWindow()
    off2.set_size_request(W, 994)
    off2.add(col2)
    off2.show_all()
    pump()
    empties = find(col2, "emptyrow")
    measured["_TASK_EMPTY_PX"] = height(empties[0], W) if empties else 0
    ag_empty = find(col2, "agempty")
    measured["_AGENDA_EMPTY_PX"] = height(ag_empty[0], W) if ag_empty else 0

    # Whatever the column costs that is NOT a header, a grid, a row or an
    # agenda label is column chrome: box spacing, margins, card borders.
    parts = (2 * measured["_HEAD_PX"]
             + measured["_GRID_WD_PX"] + weeks * measured["_GRID_ROW_PX"]
             + measured["_GRID_PAD_PX"] + measured["_AGSEC_PX"]
             + measured["_TASK_EMPTY_PX"] + measured["_AGENDA_EMPTY_PX"]
             + measured["_COL_SPACING_PX"])
    measured["_COL_CHROME_PX"] = height(col2, W) - parts

    print("measured against the live column (%d-week month, %dpx wide):\n" %
          (weeks, W))
    # A constant can only be calibrated against a widget that actually rendered.
    # find() returns nothing when the seeded scenario never produces the row
    # (e.g. the "+N more" tail only exists when the card cannot fit every row,
    # which it can at this geometry), and `one()` then reports 0. Comparing a
    # missing measurement against the constant used to print
    # "over-reserves 34, starves the cards" for .moretail, which was false
    # twice over: nothing was measured, and widgets.py never reads
    # _MORE_ROW_PX at all.
    src = open(os.path.join(os.path.dirname(widgets.__file__),
                            "widgets.py"), encoding="utf-8").read()

    bad = 0
    for name in sorted(measured):
        real = measured[name]
        cur = getattr(widgets, name, None)
        if cur is None:
            print("  %-18s %4d   (not a constant in widgets.py)" % (name, real))
            continue
        if real == 0:
            uses = src.count(name) - 1        # minus its own definition
            print("  %-18s   --     widgets.py %4d   (not rendered in this "
                  "scenario, nothing to calibrate against; %s)"
                  % (name, cur,
                     "read in %d place(s)" % uses if uses
                     else "DEAD: read nowhere in widgets.py"))
            continue
        drift = cur - real
        flag = ""
        if drift < 0:
            flag = "  <-- UNDER-COUNTS by %d, clips the column" % -drift
            bad += 1
        elif drift > 3:
            flag = "  <-- over-reserves %d, starves the cards" % drift
            bad += 1
        print("  %-18s real %4d   widgets.py %4d%s" % (name, real, cur, flag))
    print("\n%s" % ("all constants track the real widgets"
                    if not bad else "%d constant(s) need updating" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
