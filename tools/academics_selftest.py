#!/usr/bin/env python3
"""
Headless selftest for the Academics Schedule + Homework views.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 tools/academics_selftest.py

Covers the parts of the timetable and the homework list that a screenshot
cannot: the overlap layout, the undo round trip over class times and
assignments (which used to call a method UndoHistory does not have, so every
one of those edits threw and did nothing), the keyboard route through the
timetable, and the bucketing of due dates.

Writes only into a throwaway NB_HOME.
"""
import os
import sys
import json
import time
import tempfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

HOME = tempfile.mkdtemp(prefix="ac-selftest-")
os.makedirs(os.path.join(HOME, ".config", "notebook"))
os.environ["NB_HOME"] = HOME

import nbapp                                              # noqa: E402
import academics                                          # noqa: E402

RESULTS = []


def check(name, cond):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def day_key(offset):
    o = nbapp.day_ordinal(time.strftime("%Y-%m-%d")) + offset
    return academics._date_key(o)


def write_store(data):
    with open(os.path.join(HOME, ".config", "notebook", "academics.json"),
              "w") as fh:
        json.dump(data, fh)


def meet(day, start, end, room=""):
    return {"day": day, "start": start, "end": end, "room": room}


# --------------------------------------------------------------- fixtures
write_store({
    "classes": [
        {"label": "Organic Chemistry", "color": "#9A7B4F", "room": "Lab B4",
         # two meetings on the SAME day, plus a three-way overlap with the
         # classes below
         "meets": [meet(0, "09:00", "11:00"), meet(0, "14:00", "15:00")]},
        {"label": "Linear Algebra", "color": "#4A5E73",
         "meets": [meet(0, "09:30", "10:30"), meet(2, "09:00", "10:00")]},
        {"label": "Modern Poetry", "color": "#6E7B57",
         "meets": [meet(0, "10:00", "12:00")]},
    ],
    "lectures": [{"cls": 0, "num": "01", "title": "Alkanes", "date": "12 Jul",
                  "meta": "", "notes": "hello", "ranges": {}}],
    "homework": [
        {"title": "Late one", "cls": 0, "due": day_key(-3), "done": False},
        {"title": "Due today", "cls": 1, "due": day_key(0), "done": False},
        {"title": "This week", "cls": 1, "due": day_key(3), "done": False},
        {"title": "Far off", "cls": 2, "due": day_key(40), "done": False},
        {"title": "Someday", "cls": -1, "due": "", "done": False},
        {"title": "Finished", "cls": 0, "due": day_key(-9), "done": True},
    ],
    "active": 0,
})

app = academics.Academics()

# ------------------------------------------------------- overlap layout
lay = app._day_layout(0)
check("every Monday meeting is laid out", len(lay) == 4)
nine = [(ci, m, s, n) for ci, m, s, n in lay if m["start"] in
        ("09:00", "09:30", "10:00")]
check("the three overlapping classes share one run", len(nine) == 3)
check("the overlapping run is split into 3 lanes",
      {n for _c, _m, _s, n in nine} == {3})
check("each overlapping class gets its own lane",
      sorted(s for _c, _m, s, _n in nine) == [0, 1, 2])
alone = [(ci, m, s, n) for ci, m, s, n in lay if m["start"] == "14:00"]
check("a class that overlaps nothing keeps the full column",
      alone and alone[0][3] == 1 and alone[0][2] == 0)
check("a class meeting twice in one day appears twice",
      sum(1 for ci, _m, _s, _n in lay if ci == 0) == 2)
check("a day with one class needs one lane",
      [n for _c, _m, _s, n in app._day_layout(2)] == [1])
check("a day with no classes lays out nothing", app._day_layout(4) == [])

# Back-to-back meetings must NOT be treated as overlapping.
app.classes[0]["meets"] = [meet(5, "09:00", "10:00"), meet(5, "10:00", "11:00")]
check("back-to-back classes each keep the full column",
      [n for _c, _m, _s, n in app._day_layout(5)] == [1, 1])
app.classes[0]["meets"] = [meet(0, "09:00", "11:00"), meet(0, "14:00", "15:00")]

# ------------------------------------------------------- homework buckets
buckets = dict((k, idxs) for k, _name, idxs in app._homework_buckets())
check("buckets are keyed, not matched on translated headings",
      set(buckets) == {"overdue", "today", "week", "later", "nodate", "done"})
check("the late assignment is the overdue one",
      [app.homework[i]["title"] for i in buckets["overdue"]] == ["Late one"])
check("a finished assignment is filed as done regardless of its date",
      [app.homework[i]["title"] for i in buckets["done"]] == ["Finished"])
check("an assignment with no date has its own group",
      [app.homework[i]["title"] for i in buckets["nodate"]] == ["Someday"])

# ------------------------------------------------------------ undo round trip
# The bug this exists for: these paths called self.undo.push(), which
# UndoHistory has never had, so the AttributeError aborted the edit before the
# model was touched at all.
before = len(app.homework)
app.undo.checkpoint("Add an Assignment")
app.homework.append({"title": "Added by the test", "cls": 0,
                     "due": day_key(1), "done": False, "note": ""})
app._save_to_disk()
app.undo.commit()
check("an assignment can be added", len(app.homework) == before + 1)
app.undo.undo()
check("undo takes the assignment back off", len(app.homework) == before)
check("undo did not disturb the lectures", len(app.lectures) == 1)
app.undo.redo()
check("redo puts it back", len(app.homework) == before + 1)
app.undo.undo()

n_meets = len(app.classes[0]["meets"])
app.undo.checkpoint("Add a Class Time")
app.classes[0]["meets"].append(meet(3, "16:00", "17:00", "Lab B4"))
app.classes[0]["meets"] = app._clean_meets(app.classes[0]["meets"])
app._save_to_disk()
app.undo.commit()
check("a class time can be added", len(app.classes[0]["meets"]) == n_meets + 1)
app.undo.undo()
check("undo takes the class time back off",
      len(app.classes[0]["meets"]) == n_meets)

# Clearing finished homework has to be reversible: it is the one bulk delete.
n_all = len(app.homework)
app.undo.checkpoint("Clear Finished Homework")
app.homework = [h for h in app.homework if not h["done"]]
app._save_to_disk()
app.undo.commit()
check("clearing finished homework removes it", len(app.homework) == n_all - 1)
app.undo.undo()
check("undo brings the finished homework back", len(app.homework) == n_all)

# A snapshot must not share the live meeting list, or it rewrites itself.
snap = app._undo_snapshot()
app.classes[0]["meets"].append(meet(6, "08:00", "09:00"))
check("a snapshot keeps its own copy of the class times",
      len(snap["classes"][0]["meets"]) != len(app.classes[0]["meets"]))
check("a snapshot carries the homework list", "homework" in snap)
app.classes[0]["meets"].pop()

# ---------------------------------------------------------- keyboard route
app._set_view("schedule")
app.grid_area.set_size_request(700, 600)
win = Gtk.OffscreenWindow()
body = app.content.get_children()[0]
app.content.remove(body)
win.add(body)
win.show_all()
for _ in range(40):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
check("the timetable can take keyboard focus", app.grid_area.get_can_focus())
check("blocks were drawn for the hit-test", len(app._blocks) > 0)


def press(keyval):
    ev = Gdk.EventKey()
    ev.type = Gdk.EventType.KEY_PRESS
    ev.keyval = keyval
    ev.state = 0
    return app._on_timetable_key(app.grid_area, ev)


check("Right selects the first block", press(Gdk.KEY_Right)
      and app._sel_block == 0)
check("Right advances", press(Gdk.KEY_Right) and app._sel_block == 1)
check("Left goes back", press(Gdk.KEY_Left) and app._sel_block == 0)
check("Left from the first wraps to the last",
      press(Gdk.KEY_Left) and app._sel_block == len(app._blocks) - 1)
check("End goes to the last", press(Gdk.KEY_End)
      and app._sel_block == len(app._blocks) - 1)
check("Home goes to the first", press(Gdk.KEY_Home) and app._sel_block == 0)
check("an unrelated key is left alone", press(Gdk.KEY_x) is False)

# Every drawn block must be hit-testable and none may be hidden underneath
# another: two blocks may share a row only if their x ranges are disjoint.
overlapping = 0
for i, (x1, y1, w1, h1, _c1, _m1) in enumerate(app._blocks):
    for x2, y2, w2, h2, _c2, _m2 in app._blocks[i + 1:]:
        if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
            overlapping += 1
check("no drawn class block covers another", overlapping == 0)

# ------------------------------------------------------------ empty states
app.classes = []
app.lectures = []
app.homework = []
app.active = -1
app._refresh_sidebar()
app._refresh_schedule()
app._refresh_homework()
check("an empty term shows the schedule empty state",
      app.sched_stack.get_visible_child_name() == "empty")
# The shipped wording is "No classes" -- short enough for the sidebar's
# max_width_chars=16 -- and it is a key in all 17 catalogs. This asserted the
# older "No classes yet", which no longer exists anywhere.
check("the sidebar says there are no classes",
      app.side_summary.get_text() == "No classes")
check("the homework list shows its empty state",
      len(app.hw_list.get_children()) == 1)
check("the clear-finished button hides with nothing finished",
      not app.hw_clear.get_visible())

# Classes but no times: still the empty state, with its own wording.
app.classes = [{"label": "Chemistry", "color": "#9A7B4F", "meets": []}]
app._refresh_sidebar()
app._refresh_schedule()
check("classes with no times still show the schedule empty state",
      app.sched_stack.get_visible_child_name() == "empty")
check("the sidebar counts a class with no lectures",
      app.side_summary.get_text() == "1 class")
app.classes[0]["meets"] = [meet(1, "09:00", "10:00")]
app._refresh_schedule()
check("one class time is enough to show the grid",
      app.sched_stack.get_visible_child_name() == "grid")

# A search box belongs to the notes view only.
app.lectures = [{"cls": 0, "num": "01", "title": "t", "date": "", "meta": "",
                 "notes": "", "ranges": {}}]
app.view = "schedule"
app._refresh_sidebar()
check("the note search stays out of the Schedule view",
      not app.search.get_visible())
app.view = "notes"
app._refresh_sidebar()
check("the note search is back on the Notes view", app.search.get_visible())

# ------------------------------------- the real actions, dialogs stubbed out
# The regression guard proper. _add_meeting / _new_homework / _edit_homework /
# _remove_homework all called self.undo.push(), which does not exist, BEFORE
# touching the model — so the AttributeError aborted each one and nothing was
# ever added, edited or removed. Driving the methods themselves (rather than
# the mutation they wrap) is the only thing that catches that.
app.classes = [{"label": "Chemistry", "color": "#9A7B4F", "room": "",
                "instructor": "", "meets": []}]
app.lectures = []
app.homework = []
app.active = -1
app.undo.reset()

app._meeting_dialog = lambda *a, **k: (0, 2, "13:00", "14:30", "Lab B4")
app._add_meeting()
check("_add_meeting really adds a class time",
      len(app.classes[0]["meets"]) == 1)
check("_add_meeting stored what the dialog returned",
      app.classes[0]["meets"][0]["start"] == "13:00")

app._homework_dialog = lambda *a, **k: ("Read chapter 4", 0, day_key(2))
app._new_homework()
check("_new_homework really adds an assignment", len(app.homework) == 1)
check("a new assignment starts unfinished", app.homework[0]["done"] is False)

app._homework_dialog = lambda *a, **k: ("Read chapter 5", 0, day_key(2))
app._edit_homework(0)
check("_edit_homework really edits it",
      app.homework[0]["title"] == "Read chapter 5")

app._confirm = lambda *a, **k: True
app._homework_dialog = lambda *a, **k: app._REMOVE
app._edit_homework(0)
check("Remove in the assignment dialog takes it off the list",
      app.homework == [])
app.undo.undo()
check("...and undo brings it back", len(app.homework) == 1)

app._meeting_dialog = lambda *a, **k: app._REMOVE
app._edit_meeting(0, app.classes[0]["meets"][0])
check("Remove in the class-time dialog takes it off the timetable",
      app.classes[0]["meets"] == [])
app.undo.undo()
check("...and undo puts the class time back",
      len(app.classes[0]["meets"]) == 1)

# Cancelling must never be read as a delete.
app._meeting_dialog = lambda *a, **k: None
app._edit_meeting(0, app.classes[0]["meets"][0])
check("cancelling the class-time dialog changes nothing",
      len(app.classes[0]["meets"]) == 1)
app._homework_dialog = lambda *a, **k: None
n_hw = len(app.homework)
app._edit_homework(0)
check("cancelling the assignment dialog changes nothing",
      len(app.homework) == n_hw)

# A first run in the Schedule view names the class instead of inventing one.
app.classes = []
app.homework = []
app.lectures = []
app._name_dialog = lambda *a, **k: "Physics"
app._meeting_dialog = lambda *a, **k: (0, 1, "11:00", "12:00", "")
app._add_meeting()
check("the first class time asks for the class name",
      [c["label"] for c in app.classes] == ["Physics"])
check("...and no phantom lecture is created alongside it", app.lectures == [])
app.classes = []
app._name_dialog = lambda *a, **k: None
app._add_meeting()
check("declining to name a class adds nothing", app.classes == [])

print("%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
