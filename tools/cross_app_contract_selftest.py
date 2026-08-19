#!/usr/bin/env python3
"""The contracts BETWEEN apps, driven through the real apps in one shared home.

Every app has its own suites and they all pass while a shared file quietly
stops meaning the same thing on both sides. calendar.json is written by Tasks
(its Add event rail), written by Calendar, and read by both plus the desktop
board -- three programs, one flat list of {date,start,end,title,cal}. A per-app
suite cannot see a disagreement there, because each one is the only app in the
room ([[who-writes-it-last]]).

So: drive Tasks for real, then open the REAL Calendar on the same home and look
for what Tasks wrote; add one from the Calendar side and look for it back in
Tasks' schedule rail; then open the desktop board and check it shows both plus
the task. Everything through the apps' own widgets and stores -- no fixtures.

    tools/guestrun.sh python3 tools/cross_app_contract_selftest.py
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = tempfile.mkdtemp(prefix="nb-crossapp-")
os.environ["NB_DRIVE_HOME_ROOT"] = ROOT
SHARED = os.path.join(ROOT, "shared")

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


def entry(d, placeholder):
    for e in d.find(Gtk.Entry):
        if (e.get_placeholder_text() or "") == placeholder:
            return e
    return None


def events_on_disk():
    path = os.path.join(SHARED, ".config", "notebook", "calendar.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("events") if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict)]


def main():
    # ---- 1. Tasks writes an event and a task -----------------------------
    d = appdrive.Drive("tasks", home=SHARED)
    try:
        ev = entry(d, "Add event")
        check("Tasks offers the Add event rail", ev is not None)
        ev.grab_focus(); d.pump()
        d.type("10:30 Dentist"); d.key("Return"); d.pump(0.4)
        task = entry(d, "Add task")
        task.grab_focus(); d.pump()
        d.type("Buy milk"); d.key("Return"); d.pump(0.3)
    finally:
        d.close()

    rows = events_on_disk()
    dentist = [r for r in rows if r.get("title") == "Dentist"]
    check("Tasks writes the event into the SHARED calendar.json",
          len(dentist) == 1, repr(rows))
    check("...in the shape Calendar and the board read "
          "({date,start,end,title,cal})",
          bool(dentist) and set(("date", "start", "end", "title", "cal"))
          <= set(dentist[0]), repr(dentist[:1]))
    check("...with the time it was given (10:30 -> 10.5)",
          bool(dentist) and abs(float(dentist[0].get("start", -1)) - 10.5) < 1e-6,
          repr(dentist[:1]))

    # ---- 2. Calendar reads it, and writes one back -----------------------
    d = appdrive.Drive("calendar", home=SHARED)
    try:
        titles = [e.get("title") for e in d.app.events]
        check("Calendar opens on the same home and SEES the Tasks event",
              "Dentist" in titles, repr(titles))
        d.app.quick.set_text("Lunch with Sam 12:30")
        d.app._on_quick_add(d.app.quick)
        d.pump(0.4)
        titles = [e.get("title") for e in d.app.events]
        check("Calendar adds its own without dropping the other",
              "Dentist" in titles and "Lunch with Sam" in titles, repr(titles))
    finally:
        d.close()

    # ---- 3. Tasks' schedule rail shows the Calendar-side event -----------
    d = appdrive.Drive("tasks", home=SHARED)
    try:
        shown = d.texts()
        check("Tasks' schedule rail shows the event CALENDAR added",
              any("Lunch with Sam" in t for t in shown), repr(
                  [t for t in shown if "Lunch" in t or "Dentist" in t]))
        check("...and still shows its own", any("Dentist" in t for t in shown))
    finally:
        d.close()

    # ---- 4. the desktop board reads both stores --------------------------
    d = appdrive.Drive("widgets", cls="Widgets", home=SHARED, size=(1024, 740))
    try:
        shown = d.texts()
        check("the desktop board shows the day's events",
              any("Dentist" in t for t in shown)
              and any("Lunch with Sam" in t for t in shown),
              repr([t for t in shown if "Dentist" in t or "Lunch" in t]))
        check("...and the task", any("Buy milk" in t for t in shown),
              repr([t for t in shown if "milk" in t]))
    finally:
        d.close()

    # ---- 5. MUTANT: prove these checks can go red ------------------------
    # Point Calendar's reader at a file nobody writes; the "Calendar sees the
    # Tasks event" contract must fail. A green suite that cannot go red is the
    # thing this project keeps catching.
    other = os.path.join(ROOT, "mutant")
    os.makedirs(os.path.join(other, ".config", "notebook"), exist_ok=True)
    d = appdrive.Drive("calendar", home=other)
    try:
        titles = [e.get("title") for e in d.app.events]
        check("MUTANT: a Calendar reading a DIFFERENT home sees no Tasks event "
              "(so check 2 is capable of failing)", "Dentist" not in titles,
              repr(titles))
    finally:
        d.close()

    shutil.rmtree(ROOT, ignore_errors=True)
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
