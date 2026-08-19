#!/usr/bin/env python3
"""
Headless selftest for the Calendar's close-down lifecycle: the once-a-minute
date-rollover poll must die with the window.

THE BUG THIS EXISTS FOR
-----------------------
__init__ armed the midnight guard with

    GLib.timeout_add_seconds(60, self._check_date_rollover)

and threw the source id away, while _on_destroy only flushed the two stores.
A GLib timeout holds a reference to its callback, so every Calendar the user
had ever closed stayed alive and kept waking once a minute for the rest of the
session — and _check_date_rollover always returned True, so nothing ever
stopped it. On the first date change after that (the OS is left running across
midnight), each of those dead windows ran _refresh against widgets GTK had
already destroyed.

The contract this pins down:
  1. An open window's poll is unchanged: it keeps returning True, and it only
     re-renders on an ACTUAL date change (once, not on every tick).
  2. A closed window's poll returns False — and touches nothing on the way out:
     self.today is not moved and _refresh is never called.
  3. destroy marks the window closed BEFORE the saves run, removes exactly the
     source id that was recorded, clears it, and writes each store once.
  4. destroy is idempotent: a second emission removes no id (the number could
     since belong to another timer) and does not save again.

DISPLAY-FREE. No window is built: the real methods are bound onto a bare
instance made with __new__, GLib is swapped for a recorder, and date.today is
frozen behind a stub so a rollover can be staged without waiting for midnight.

  PYTHONPATH=<overlay>/opt/notebook/de python3 calendar_lifecycle_selftest.py
"""
import inspect
import os
import sys
from datetime import date

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


class FrozenDate(date):
    """date with a settable today(), so a rollover can be staged. A real date
    subclass, so every comparison in the app behaves normally."""
    _now = date(2026, 7, 29)

    @classmethod
    def today(cls):
        return cls._now


class FakeGLib(object):
    """Records what the app asks of the main loop instead of arming anything."""
    def __init__(self):
        self.armed = []
        self.removed = []
        self.next_id = 4100

    def timeout_add_seconds(self, secs, fn, *a):
        self.next_id += 1
        self.armed.append((secs, fn, self.next_id))
        return self.next_id

    def source_remove(self, sid):
        self.removed.append(sid)
        return True


def bare_calendar(glib, closed=False, rollover_id=0):
    """A Calendar carrying only the lifecycle state, with the two stores and
    the re-render replaced by counters."""
    cal = cal_app.Calendar.__new__(cal_app.Calendar)
    cal._closed = closed
    cal._status_tok = 0
    cal._status_timer = 0
    cal._rollover_id = rollover_id
    cal.today = FrozenDate._now
    cal.refreshes = 0
    cal.saved = []
    cal.closed_when_saved = []

    def _refresh():
        cal.refreshes += 1
    cal._refresh = _refresh

    def _save_events():
        cal.saved.append("events")
        cal.closed_when_saved.append(cal._closed)
    cal._save_events = _save_events

    def _save_calendars():
        cal.saved.append("calendars")
        cal.closed_when_saved.append(cal._closed)
    cal._save_calendars = _save_calendars
    return cal


GLIB = FakeGLib()
REAL_GLIB, REAL_DATE = cal_app.GLib, cal_app.date
cal_app.GLib, cal_app.date = GLIB, FrozenDate
try:
    # -- 0. the source id is actually kept at arm time ------------------------
    # __init__ needs a display, so this one is read off the source: the whole
    # defect was that the return value of the arming call went nowhere.
    src = inspect.getsource(cal_app.Calendar.__init__)
    check("__init__ stores the rollover source id",
          "self._rollover_id = GLib.timeout_add_seconds(" in
          " ".join(src.split()),
          [ln.strip() for ln in src.splitlines()
           if "timeout_add_seconds" in ln])

    # -- 1. an open window: a tick with no date change does nothing -----------
    cal = bare_calendar(GLIB, rollover_id=4101)
    keep = cal._check_date_rollover()
    check("an open window keeps polling on an unchanged date", keep is True)
    check("...and does not re-render", cal.refreshes == 0, cal.refreshes)
    check("...and leaves today alone", cal.today == date(2026, 7, 29), cal.today)

    # -- 2. an open window: a real date change re-renders exactly once --------
    FrozenDate._now = date(2026, 7, 30)
    keep = cal._check_date_rollover()
    check("a date change re-renders once", cal.refreshes == 1, cal.refreshes)
    check("...and moves today to the new date",
          cal.today == date(2026, 7, 30), cal.today)
    check("...and keeps the poll alive", keep is True)
    keep = cal._check_date_rollover()
    check("...and the next tick is quiet again",
          cal.refreshes == 1 and keep is True, cal.refreshes)

    # -- 3. a closed window: the poll stops and touches nothing ---------------
    # Staged with a date change pending, which is exactly when the stale timer
    # used to reach into destroyed widgets.
    dead = bare_calendar(GLIB, closed=True, rollover_id=4102)
    was = dead.today
    FrozenDate._now = date(2026, 8, 1)
    keep = dead._check_date_rollover()
    check("a closed window stops polling", keep is False, keep)
    check("...without re-rendering", dead.refreshes == 0, dead.refreshes)
    check("...and without moving today", dead.today == was, dead.today)

    # Control: the SAME instance, same pending date change, only _closed
    # flipped. If this did not fire, the checks above would pass for the wrong
    # reason (a poll that never does anything at all).
    dead._closed = False
    keep = dead._check_date_rollover()
    check("control: only the closed flag was stopping it",
          keep is True and dead.refreshes == 1 and
          dead.today == date(2026, 8, 1),
          (keep, dead.refreshes, dead.today))

    # -- 4. destroy: close, disarm, then save --------------------------------
    GLIB.removed = []
    cal = bare_calendar(GLIB, rollover_id=4242)
    day_at_close = cal.today
    cal._on_destroy(object())
    check("destroy marks the window closed",
          cal._closed is True)
    check("...before the stores are written",
          cal.closed_when_saved == [True, True], cal.closed_when_saved)
    check("...removes exactly the recorded source",
          GLIB.removed == [4242], GLIB.removed)
    check("...clears the source id", cal._rollover_id == 0, cal._rollover_id)
    check("...and saves each store once",
          cal.saved == ["events", "calendars"], cal.saved)

    # -- 5. a second destroy is harmless -------------------------------------
    cal._on_destroy(object())
    check("a second destroy removes no source id",
          GLIB.removed == [4242], GLIB.removed)
    check("...and does not write the stores again",
          cal.saved == ["events", "calendars"], cal.saved)

    # A window closed before its timer was ever armed must not remove id 0.
    GLIB.removed = []
    never = bare_calendar(GLIB, rollover_id=0)
    never._on_destroy(object())
    check("destroy with no armed timer removes nothing",
          GLIB.removed == [] and never.saved == ["events", "calendars"],
          (GLIB.removed, never.saved))

    # -- 6. and the poll of a destroyed window is dead ------------------------
    FrozenDate._now = date(2026, 9, 9)
    keep = cal._check_date_rollover()
    check("the poll of a destroyed window returns False and is inert",
          keep is False and cal.refreshes == 0 and
          cal.today == day_at_close, (keep, cal.refreshes, cal.today))
finally:
    cal_app.GLib, cal_app.date = REAL_GLIB, REAL_DATE

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
print("RESULT: %s" % ("PASS" if all(RESULTS) else "FAIL"))
sys.exit(0 if all(RESULTS) else 1)
