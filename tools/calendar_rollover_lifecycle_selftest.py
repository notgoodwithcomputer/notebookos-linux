#!/usr/bin/env python3
"""Headless ownership checks for Calendar's midnight rollover timer."""
import os
import sys
from datetime import date as real_date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import calendar as calmod  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Clock:
    current = real_date(2026, 8, 5)

    @classmethod
    def today(cls):
        return cls.current


real_clock = calmod.date
calmod.date = Clock
try:
    win = calmod.Calendar.__new__(calmod.Calendar)
    win._closed = False
    win.today = Clock.current
    win.refreshes = 0
    win._refresh = lambda: setattr(win, "refreshes", win.refreshes + 1)
    check(win._check_date_rollover() is True and win.refreshes == 0,
          "an unchanged live date keeps polling without repainting")

    Clock.current = real_date(2026, 8, 6)
    check(win._check_date_rollover() is True,
          "a live rollover keeps the minute poll active")
    check(win.today == Clock.current and win.refreshes == 1,
          "a live date change updates state and refreshes exactly once")

    win._closed = True
    prior = win.today
    Clock.current = real_date(2026, 8, 7)
    check(win._check_date_rollover() is False,
          "a closed Calendar removes its repeating callback")
    check(win.today == prior and win.refreshes == 1,
          "a closed rollover touches neither state nor widgets")
finally:
    calmod.date = real_clock


win = calmod.Calendar.__new__(calmod.Calendar)
win._closed = False
win._status_tok = 0
win._status_timer = 66
win._rollover_id = 77
events = []
win._save_events = lambda: events.append(("events", win._closed))
win._save_calendars = lambda: events.append(("calendars", win._closed))
removed = []
real_remove = calmod.GLib.source_remove
calmod.GLib.source_remove = lambda source_id: removed.append(source_id)
try:
    win._on_destroy()
    win._on_destroy()
finally:
    calmod.GLib.source_remove = real_remove

check(win._closed is True, "destroy marks the Calendar closed")
check(win._status_tok == 1,
      "destroy invalidates pending status callbacks exactly once")
check(removed == [66, 77] and
      win._status_timer == 0 and win._rollover_id == 0,
      "destroy removes exactly its recorded sources and clears their IDs")
check(events == [("events", True), ("calendars", True)],
      "final stores save once, after the closed gate is raised")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
