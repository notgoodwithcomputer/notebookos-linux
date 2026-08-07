#!/usr/bin/env python3
"""
Headless selftest for the Calendar's repeating-series arithmetic.

Display-free on purpose: it drives Calendar._extend_series on a bare instance
(no window, no dialog, no Gtk main loop), because the defect it guards is pure
date arithmetic and a test that needs a screen cannot run in a build.

A repeat is stored as REAL dated records with a capped run (REPEAT_LIMIT), and
_extend_series tops that run up every time the app opens. The invariant:

  1. A monthly series keeps the day of the month it STARTED on. Stepping one
     turn off the LAST occurrence compounds the short-month clamp, so a "31st"
     series that has just written a 28 February goes on from the 28th and never
     comes back — rent day silently moves three days earlier, for good.
  2. A yearly series behaves the same way across a leap day: 29 February must
     return on the next leap year, not stay clamped to the 28th forever.
  3. Day / week / fortnight rules, which carry no clamp, are unchanged.
  4. The top-up stays bounded and idempotent: no duplicate dates, at most
     REPEAT_LIMIT new records per series, and nothing at all on a second open
     with the same date.

Run as:
  PYTHONPATH=<overlay>/opt/notebook/de python3 calendar_selftest.py
"""
import os
import sys
from datetime import date, timedelta

DE = os.environ.get("NB_DE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "buildroot", "board", "notebookos", "rootfs-overlay", "opt", "notebook",
    "de")
if os.path.isdir(DE):
    sys.path.insert(0, DE)
# calendar.py deliberately shadows the stdlib module of the same name.
sys.modules.pop("calendar", None)
import calendar as cal_app                                      # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def bare_calendar():
    """A Calendar with just the state the series arithmetic touches — built
    without __init__ so nothing here needs a display or a config directory."""
    cal = cal_app.Calendar.__new__(cal_app.Calendar)
    cal.events = []
    cal.class_events = []
    cal._orphans = []
    cal._seen = set()
    cal.today = date.today()
    return cal


def seed_series(cal, start, rule, title="Rent"):
    """Write one series exactly as _create_event does: the full capped run,
    every occurrence a real record sharing one series id."""
    sid = "series-" + rule
    fields = {"start": 9.0, "end": 10.0, "title": title, "cal": "Personal"}
    for d in cal_app._repeat_dates(start, rule):
        cal._new_event(d, fields, rule, sid)
    return sid


def dates_of(cal, title):
    return sorted(e["date"] for e in cal.events if e["title"] == title)


# -- 1. a monthly series keeps its day of the month --------------------------
cal = bare_calendar()
seed_series(cal, date(2026, 1, 31), "month")
before = dates_of(cal, "Rent")
check("the original run is anchored on the 31st",
      before[0] == date(2026, 1, 31) and before[2] == date(2026, 3, 31),
      before[:3])
check("...clamping only the short month",
      before[1] == date(2026, 2, 28), before[1])

# March 2026: the horizon (400 days) now reaches past February 2027, so the
# top-up has to step over a clamped occurrence.
cal._extend_series(date(2026, 3, 1))
after = dates_of(cal, "Rent")
grown = [d for d in after if d not in before]
check("the run is topped up past the end of its written year",
      date(2027, 1, 31) in grown, grown)
check("February still clamps to the 28th", date(2027, 2, 28) in grown, grown)
check("March goes back to the 31st, not on from the 28th",
      date(2027, 3, 31) in grown and date(2027, 3, 28) not in grown, grown)
check("no month is written twice", len(after) == len(set(after)),
      [d for d in after if after.count(d) > 1])
check("the top-up stays bounded",
      len(grown) <= cal_app.REPEAT_LIMIT["month"], len(grown))

# a second open on the same day must add nothing
cal._extend_series(date(2026, 3, 1))
check("re-opening the same day adds nothing",
      dates_of(cal, "Rent") == after, len(dates_of(cal, "Rent")) - len(after))

# -- 2. a yearly series returns to 29 February -------------------------------
cal = bare_calendar()
seed_series(cal, date(2024, 2, 29), "year", title="Birthday")
before = dates_of(cal, "Birthday")
check("a 29 February series is written from the leap day",
      before[0] == date(2024, 2, 29) and before[1] == date(2025, 2, 28),
      before)
# The written run ends on the 2028 leap day; the top-up has to cross three
# clamped 28ths and still find the 29th in 2032.
cal._extend_series(date(2027, 3, 1))
grown = [d for d in dates_of(cal, "Birthday") if d not in before]
check("the non-leap years in between clamp to the 28th",
      date(2029, 2, 28) in grown and date(2031, 2, 28) in grown, grown)
check("the next leap year comes back to the 29th",
      date(2032, 2, 29) in grown and date(2032, 2, 28) not in grown, grown)

# -- 3. the unclamped rules are unchanged ------------------------------------
for rule, step in (("day", 1), ("week", 7), ("fortnight", 14)):
    cal = bare_calendar()
    start = date.today() - timedelta(days=3)
    seed_series(cal, start, rule, title=rule)
    cal._extend_series(date.today())
    got = dates_of(cal, rule)
    gaps = {(b - a).days for a, b in zip(got, got[1:])}
    check("a %sly rule still steps by %d days" % (rule, step),
          gaps == {step}, sorted(gaps))
    check("...and reaches the %s horizon" % rule,
          got[-1] >= date.today() + timedelta(
              days=cal_app.REPEAT_AHEAD[rule] - step), got[-1])

# -- 4. a series already long enough is left alone ---------------------------
cal = bare_calendar()
seed_series(cal, date.today(), "month", title="Standing order")
kept = dates_of(cal, "Standing order")
cal._extend_series(date.today() - timedelta(days=365))
check("a run already past the horizon is not extended",
      dates_of(cal, "Standing order") == kept)

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
