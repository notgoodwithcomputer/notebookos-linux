#!/usr/bin/env python3
"""Undo must land ONE step back from what is on screen -- never further.

THE CLASS: nbapp.UndoHistory records a step when an app calls touch() (typing)
or checkpoint()+commit() (a structural edit). Apps whose ADDITIONS do neither
-- a task typed into Tasks, an event quick-added in Calendar, a contact typed
into Contacts -- left the newest recorded state as the one from launch. Undo
of a later Delete restored THAT: add three tasks, delete one by mistake,
Ctrl+Z, and all three were gone (and written to disk). checkpoint() now pushes
the on-screen state first, so the step it brackets undoes exactly itself.

The suite drives the REAL apps through tools/appdrive.py: real entries, real
key ladder, real store, real Ctrl+Z.

    tools/guestrun.sh python3 tools/undo_baseline_selftest.py
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["NB_DRIVE_HOME_ROOT"] = tempfile.mkdtemp(prefix="nb-undo-baseline-")

import appdrive  # noqa: E402
from gi.repository import Gtk  # noqa: E402

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def entry_with_placeholder(d, text):
    for e in d.find(Gtk.Entry):
        if (e.get_placeholder_text() or "") == text:
            return e
    return None


def tasks_case():
    d = appdrive.Drive("tasks")
    try:
        add = entry_with_placeholder(d, "Add task")
        add.grab_focus(); d.pump()
        for t in ("Buy milk", "Call dentist", "Pay rent"):
            d.type(t); d.key("Return"); d.pump(0.15)
        titles = lambda: [t["title"] for t in d.app.tasks]
        check("Tasks: three typed tasks are on the model", titles() == ["Buy milk", "Call dentist", "Pay rent"], repr(titles()))
        d.app._delete_task(len(d.app.tasks) - 1); d.pump(0.2)
        check("Tasks: Delete removes one", titles() == ["Buy milk", "Call dentist"], repr(titles()))
        d.key("z", ctrl=True); d.pump(0.3)
        check("Tasks: Undo Delete brings back ONLY the deleted task -- the other two survive",
              titles() == ["Buy milk", "Call dentist", "Pay rent"], repr(titles()))

        # ---- the other half: a plain Ctrl+Z with unrecorded additions ------
        # tick one (a recorded step), then type more tasks (unrecorded), then
        # Ctrl+Z: it must not step straight back over the new tasks to the
        # ticked state and write that to disk. The additions become one step
        # of their own (Redo returns them); the tick is the step after.
        d.app._toggle(None, 0); d.pump(0.2)
        for t in ("Walk dog", "Water plants"):
            d.type(t); d.key("Return"); d.pump(0.15)
        d.key("z", ctrl=True); d.pump(0.3)
        # Whether the app records each addition as its own step (Tasks does
        # now: "Undo New Task") or the history batches unrecorded additions,
        # ONE Ctrl+Z may take back the newest addition(s) only: the three
        # older tasks and the tick must all still be there.
        full = ["Buy milk", "Call dentist", "Pay rent", "Walk dog", "Water plants"]
        got = titles()
        check("Tasks: Ctrl+Z after additions steps back over additions only -- older tasks and the tick survive",
              full[:len(got)] == got and 3 <= len(got) < 5
              and d.app.tasks[0].get("done") is True, repr([(t["title"], t.get("done")) for t in d.app.tasks]))
        d.key("z", ctrl=True, shift=True); d.pump(0.3)
        check("Tasks: ...and Redo brings them back",
              titles() == full, repr(titles()))
    finally:
        d.close()


def calendar_case():
    d = appdrive.Drive("calendar")
    try:
        app = d.app
        for t in ("Dentist 10:00", "Lunch with Sam 12:30", "Gym 18:00"):
            app.quick.set_text(t)
            app._on_quick_add(app.quick); d.pump(0.15)
        names = lambda: sorted(e.get("title", "") for e in app.events)
        check("Calendar: three quick-added events are on the model", len(app.events) == 3, repr(names()))
        victim = app.events[-1]
        # the app's own delete path (whatever it is called), then undo
        deleter = getattr(app, "_delete_event", None) or getattr(app, "_remove_event", None)
        if deleter is None:
            check("Calendar: an event delete path exists", False, "no _delete_event/_remove_event")
            return
        try:
            deleter(victim)
        except TypeError:
            deleter(app.events.index(victim))
        d.pump(0.2)
        check("Calendar: Delete removes one", len(app.events) == 2, repr(names()))
        d.key("z", ctrl=True); d.pump(0.3)
        check("Calendar: Undo Delete brings back ONLY the deleted event -- the other two survive",
              len(app.events) == 3, repr(names()))
    finally:
        d.close()


def main():
    tasks_case()
    calendar_case()
    shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)
    print("%d checks, %d passed, %d FAILED" % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
