#!/usr/bin/env python3
"""
Headless selftest for the classes Academics mirrors onto the Calendar.

The invariant that matters most is NEGATIVE: a mirrored class must never be
written into calendar.json. Academics owns class times, so a copy in the
Calendar's own store would be a second source of truth that drifts the moment
the timetable changes — and would survive after the class was deleted.

  1. Expansion — a weekly pattern becomes dated occurrences on the right days
     at the right times, bounded, with the room falling back to the class's.
  2. Isolation — derived events never reach the store, through every path that
     writes it, including a save that happens after the mirror is loaded.
  3. Read-only — a mirrored event is flagged so the click path refuses to edit
     it in Calendar.
  4. Resilience — a missing, unparseable or wrong-shaped academics.json means
     no classes mirrored, never a broken Calendar.

Run as:
  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de python3 calendar_mirror_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                             # noqa: E402,F401

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def fresh_home():
    home = tempfile.mkdtemp(prefix="nb-calmir-")
    os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
    return home


def put(home, name, obj):
    with open(os.path.join(home, ".config", "notebook", name), "w",
              encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def new_cal(home):
    os.environ["NB_HOME"] = home
    for mod in ("calendar", "nbapp"):
        sys.modules.pop(mod, None)
    import calendar as cal_app
    return cal_app, cal_app.Calendar()


SCHEDULE = {
    "classes": [
        {"label": "Organic Chemistry", "name": "Organic Chemistry",
         "color": "#9A7B4F", "room": "Lab B4",
         "meets": [{"day": 0, "start": "14:00", "end": "15:30",
                    "room": "Lab B4"},
                   {"day": 2, "start": "14:00", "end": "15:30"}]},
        {"label": "Linear Algebra", "name": "Linear Algebra",
         "color": "#4A5E73",
         "meets": [{"day": 1, "start": "09:00", "end": "10:00",
                    "room": "Hall 2"}]}],
    "lectures": [], "homework": [], "active": -1,
}


# -- 1. expansion ------------------------------------------------------------
home = fresh_home()
put(home, "academics.json", SCHEDULE)
mod, cal = new_cal(home)

today = date.today()
monday = today - timedelta(days=today.weekday())
mon = cal._events_on(monday)
tue = cal._events_on(monday + timedelta(days=1))
wed = cal._events_on(monday + timedelta(days=2))
thu = cal._events_on(monday + timedelta(days=3))

check("a Monday class lands on Monday",
      [e["title"] for e in mon] == ["Organic Chemistry"], mon)
check("...at the time the timetable says",
      (mon[0]["start"], mon[0]["end"]) == (14.0, 15.5) if mon else False,
      mon[0] if mon else None)
check("a class meeting twice a week lands on both days",
      [e["title"] for e in wed] == ["Organic Chemistry"], wed)
check("a second class lands on its own day",
      [e["title"] for e in tue] == ["Linear Algebra"], tue)
check("a day with no class stays empty", thu == [], thu)
check("a meeting with no room of its own inherits the class's",
      wed[0].get("room") == "Lab B4" if wed else False,
      wed[0] if wed else None)
check("a mirrored class carries its own colour",
      cal._event_color(mon[0]) == "#9A7B4F" if mon else False)
check("the expansion is bounded, not endless",
      0 < len(cal.class_events) < 2000, len(cal.class_events))
check("classes reach several months ahead",
      any(e["date"] > today + timedelta(days=100) for e in cal.class_events))


# -- 2. isolation: the store must never learn about them ---------------------
check("mirrored classes are not in the Calendar's own event list",
      all(not e.get("derived") for e in cal.events), len(cal.events))

cal._save_events()
store = os.path.join(home, ".config", "notebook", "calendar.json")
on_disk = json.load(open(store)) if os.path.exists(store) else []
check("saving does not write a single class into calendar.json",
      not any("Chemistry" in json.dumps(r) or "Algebra" in json.dumps(r)
              for r in on_disk), on_disk[:2])
check("...and the store is still a list", isinstance(on_disk, list))

# A real event added alongside must still round-trip.
cal.events.append(cal._norm_event(
    {"date": monday.isoformat(), "start": 11.0, "end": 12.0,
     "title": "Dentist", "cal": cal._cal_names()[0]}))
cal._save_events()
on_disk = json.load(open(store))
titles = [r.get("title") for r in on_disk]
check("a real event beside a mirrored one still saves",
      titles == ["Dentist"], titles)

# Reopening must not have absorbed the classes.
cal.destroy()
mod, cal2 = new_cal(home)
check("reopening does not adopt the classes into the store",
      [e["title"] for e in cal2.events] == ["Dentist"],
      [e["title"] for e in cal2.events])
check("...and still shows them on the day",
      sorted(e["title"] for e in cal2._events_on(monday))
      == ["Dentist", "Organic Chemistry"],
      cal2._events_on(monday))
cal2.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 3. read-only ------------------------------------------------------------
home = fresh_home()
put(home, "academics.json", SCHEDULE)
mod, cal = new_cal(home)
derived = cal._events_on(monday)
check("a mirrored class is flagged as belonging to another app",
      derived and derived[0].get("derived") == "academics",
      derived[0] if derived else None)
check("the flag names WHICH app owns it, not just that it is derived",
      derived[0].get("derived") == "academics" if derived else False)
cal.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 4. resilience -----------------------------------------------------------
for label, blob in (("missing", None),
                    ("unparseable", "{oh no"),
                    ("a bare list", "[1,2,3]"),
                    ("wrong types", json.dumps({"classes": 7})),
                    ("junk meetings", json.dumps({"classes": [
                        {"name": "X", "meets": [{"day": "Tuesday",
                                                 "start": "nope"},
                                                {"day": 99, "start": "09:00"},
                                                {"day": 1, "start": "25:00"},
                                                "not-a-dict"]}]})),
                    ("a class with no name", json.dumps({"classes": [
                        {"name": "", "meets": [{"day": 1, "start": "09:00"}]}]}))):
    home = fresh_home()
    if blob is not None:
        put(home, "academics.json", blob)
    try:
        mod, cal = new_cal(home)
        check("Calendar opens with %s academics store" % label, True)
        check("  ...and mirrors nothing from it",
              cal.class_events == [], len(cal.class_events))
        cal.destroy()
    except Exception as exc:                                    # noqa: BLE001
        check("Calendar opens with %s academics store" % label, False,
              repr(exc))
    shutil.rmtree(home, ignore_errors=True)

# An end time that is missing or before the start must still produce a sane
# block rather than a zero-height or negative one.
home = fresh_home()
put(home, "academics.json", {"classes": [
    {"name": "Seminar", "meets": [{"day": 1, "start": "09:00"},
                                  {"day": 2, "start": "10:00",
                                   "end": "09:00"}]}]})
mod, cal = new_cal(home)
bad = [e for e in cal.class_events if e["date"].weekday() in (1, 2)]
check("a missing or backwards end time still gives a positive block",
      bad and all(e["end"] > e["start"] for e in bad),
      [(e["start"], e["end"]) for e in bad[:2]])
cal.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 5. work shifts ----------------------------------------------------------
# Shifts live in Calendar rather than a dedicated app, so they must reuse the
# calendar's own machinery: a shift IS an event, on its own calendar.
home = fresh_home()
mod, cal = new_cal(home)
check("no Work calendar until a shift is added",
      "Work" not in [c["name"] for c in cal.calendars],
      [c["name"] for c in cal.calendars])

cal.sel = monday
cal._create_shift(monday, "Late shift", 9.0, 17.0, "none")
check("adding a shift creates the Work calendar",
      "Work" in [c["name"] for c in cal.calendars],
      [c["name"] for c in cal.calendars])
shifts = [e for e in cal._events_on(monday) if e["title"] == "Late shift"]
check("a day shift is one block at the times given",
      len(shifts) == 1 and (shifts[0]["start"], shifts[0]["end"]) == (9.0, 17.0),
      shifts)
check("...on the Work calendar", shifts[0]["cal"] == "Work" if shifts else False)

cal._create_shift(monday, "Weekly", 6.0, 14.0, "week")
weekly = [e for e in cal.events if e["title"] == "Weekly"]
check("a weekly shift writes the whole run", len(weekly) == 53, len(weekly))
check("...all sharing one series so it can be removed as a whole",
      len({e["series"] for e in weekly}) == 1,
      {e["series"] for e in weekly})
check("a shift can start before the event dialog's earliest hour",
      any(e["start"] == 6.0 for e in weekly))

# The case an ordinary event cannot express at all.
cal._create_shift(monday, "Night", 22.0, 6.0, "none")
eve = [e for e in cal._events_on(monday) if e["title"] == "Night"]
morn = [e for e in cal._events_on(monday + timedelta(days=1))
        if e["title"] == "Night"]
check("a night shift puts an evening block on the day it starts",
      len(eve) == 1 and (eve[0]["start"], eve[0]["end"]) == (22.0, 24.0), eve)
check("...and the rest on the following morning",
      len(morn) == 1 and (morn[0]["start"], morn[0]["end"]) == (0.0, 6.0), morn)
check("...joined as one shift, not two unrelated events",
      eve and morn and eve[0]["series"] == morn[0]["series"])
check("every block is drawable (end always after start)",
      all(e["end"] > e["start"] for e in cal.events),
      [(e["title"], e["start"], e["end"]) for e in cal.events
       if e["end"] <= e["start"]][:3])

# Shifts are the user's own events, so unlike classes they DO persist.
cal._save_events()
on_disk = json.load(open(os.path.join(home, ".config", "notebook",
                                      "calendar.json")))
check("shifts are saved (they are the user's, unlike mirrored classes)",
      any(r.get("cal") == "Work" for r in on_disk))
check("...and no class slipped in beside them",
      not any(r.get("cal") == "Classes" for r in on_disk))
cal.destroy()
shutil.rmtree(home, ignore_errors=True)

check("a shift length reads as words",
      (mod._fmt_hours(8.0), mod._fmt_hours(7.5), mod._fmt_hours(1.0),
       mod._fmt_hours(0.75))
      == ("8 hours", "7 hours 30 minutes", "1 hour", "45 minutes"),
      (mod._fmt_hours(8.0), mod._fmt_hours(1.0)))


print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
