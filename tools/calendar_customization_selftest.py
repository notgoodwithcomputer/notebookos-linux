#!/usr/bin/env python3
"""Headless checks for task 039 event fields and series exceptions."""
import os
import sys
from datetime import date

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)
sys.modules.pop("calendar", None)
import calendar as app  # noqa: E402


def check(label, condition):
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        raise AssertionError(label)


def bare():
    obj = app.Calendar.__new__(app.Calendar)
    obj.calendars = [dict(app.DEFAULT_CAL)]
    obj.cals_on = {"Personal": True, "Work": True}
    obj.events = []
    obj.class_events = []
    obj._seen = set()
    obj._orphans = []
    obj.today = date(2026, 1, 1)
    return obj


class UnellipsizedLayout:
    def is_ellipsized(self):
        return False


class FittingDowLabel:
    def get_layout(self):
        return UnellipsizedLayout()


c = bare()
c._dow_labels = [FittingDowLabel()]
dow_shorten_scheduled = []
original_idle_add = app.GLib.idle_add
app.GLib.idle_add = lambda callback: dow_shorten_scheduled.append(callback)
try:
    c._dow_fit(None, None)
finally:
    app.GLib.idle_add = original_idle_add
check("fitting weekday headers do not schedule shortening",
      not dow_shorten_scheduled)


c = bare()
raw = {"id": "old", "date": "2026-01-02", "start": 9, "end": 10,
       "title": "Old", "cal": "Personal"}
old = c._norm_event(raw)
check("old record migration defaults", old["location"] == "" and
      old["notes"] == "" and not old["all_day"])

event = c._new_event(date(2026, 1, 3), {
    "title": "Picnic", "cal": "Personal", "start": 0.0, "end": 24.0,
    "location": "Park", "notes": "Bring lunch", "all_day": True})
record = c._event_record(event)
check("all-day record omits clock", "start" not in record and "end" not in record)
roundtrip = c._norm_event(record)
check("new fields round trip", (roundtrip["location"], roundtrip["notes"],
      roundtrip["all_day"]) == ("Park", "Bring lunch", True))
all_day, timed = c._partition_day_events(date(2026, 1, 3))
check("day model partitions all-day", all_day == [event] and timed == [])
event["cal"] = "Work"
check("calendar reassignment persists", c._event_record(event)["cal"] == "Work")

fortnight = app._repeat_dates(date(2026, 12, 20), "fortnight")
check("two-week rule crosses year", fortnight[:2] ==
      [date(2026, 12, 20), date(2027, 1, 3)])
monthly = app._repeat_dates(date(2026, 1, 31), "month")
check("monthly 31 uses anchor clamp", monthly[:3] ==
      [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])
ended = app._repeat_dates(date(2026, 1, 1), "week", date(2026, 1, 15))
check("end date is inclusive", ended ==
      [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)])

c = bare()
c._create_event(date(2026, 1, 1), {"title": "Standup", "cal": "Personal",
                "start": 9.0, "end": 10.0}, "week")
occ = sorted(c.events, key=lambda e: e["date"])[2]
original = occ["date"]
c._edit_series_scope(occ, original.replace(day=16), {"title": "Moved"}, "one")
before = len(c.events)
c._extend_series(date(2026, 12, 1))
check("detached occurrence survives expansion", occ in c.events and
      occ["date"] == date(2026, 1, 16) and len([e for e in c.events
      if e.get("pattern_date") == "2026-01-15"]) == 1)
check("detached pattern date is not regenerated", not any(
      e["date"] == original and e is not occ for e in c.events))

cut = sorted((e for e in c.events if not e.get("detached")),
             key=lambda e: e["date"])[4]
c._delete_series_scope(cut, "following")
check("delete following truncates", all(e["date"] < cut["date"]
      for e in c.events if e.get("series") == cut.get("series")))
c._extend_series(date(2027, 1, 1))
check("truncation survives expansion", all(e["date"] < cut["date"]
      for e in c.events if e.get("series") == cut.get("series")))

print("PASS calendar customization selftest")
