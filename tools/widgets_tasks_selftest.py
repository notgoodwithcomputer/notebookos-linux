#!/usr/bin/env python3
"""Headless test for the desktop widget column's task store.

Guards the data-loss race: the column holds a SNAPSHOT of tasks.json taken when
the desktop home last came back, and the Tasks app can write newer tasks after
that (the app-active flag it watches clears before that app finishes saving).
Ticking a checkbox must apply that one change to what is on disk, never write
the stale snapshot back over it."""
import json
import os
import shutil
import tempfile

os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbwidgets-")
CFG = os.path.join(os.environ["NB_HOME"], ".config", "notebook")
os.makedirs(CFG, exist_ok=True)
TASKS = os.path.join(CFG, "tasks.json")

import gi                                                   # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402,F401

import widgets  # noqa: E402

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


def write(tasks):
    with open(TASKS, "w") as fh:
        json.dump(tasks, fh)


def read():
    with open(TASKS) as fh:
        return json.load(fh)


# --- the race: a task added by the Tasks app after the column's snapshot ---
write([{"text": "Ring the plumber", "done": False},
       {"text": "Post the card", "done": False}])
col = widgets.Widgets()
check("column read both tasks", len(col.tasks) == 2)

# the Tasks app adds a third task and completes the second, AFTER the snapshot
write([{"text": "Ring the plumber", "done": False},
       {"text": "Post the card", "done": True},
       {"text": "Book the ferry", "done": False}])

col._toggle_task(0)                     # user ticks the first row on the desktop
disk = read()
check("tick was applied", disk[0]["done"] is True)
check("newer task survived the tick", len(disk) == 3)
check("newer task is the right one",
      any(t["text"] == "Book the ferry" for t in disk))
check("the app's own completion survived",
      [t for t in disk if t["text"] == "Post the card"][0]["done"] is True)
check("column now shows the live store", len(col.tasks) == 3)

# --- a task deleted elsewhere must not be resurrected by a tick ---
write([{"text": "Book the ferry", "done": False}])
col.tasks = [{"text": "Ring the plumber", "done": True},
             {"text": "Book the ferry", "done": False}]
col._rebuild_tasks()
col._toggle_task(0)                     # the row for a task that no longer exists
disk = read()
check("deleted task not resurrected",
      not any(t["text"] == "Ring the plumber" for t in disk))
check("store otherwise untouched", len(disk) == 1)

# --- ordinary case still works, and progress keeps up ---
write([{"text": "One", "done": False}, {"text": "Two", "done": False}])
col2 = widgets.Widgets()
col2._toggle_task(1)
check("plain tick writes through", read()[1]["done"] is True)
check("progress read-out updated", col2._progress.get_text() == "1 / 2 done")

shutil.rmtree(os.environ["NB_HOME"], ignore_errors=True)
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
