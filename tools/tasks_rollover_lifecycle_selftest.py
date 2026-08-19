#!/usr/bin/env python3
"""Headless ownership checks for Tasks' day-rollover timer."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import tasks  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Clock:
    day = (2026, 8, 5)

    @classmethod
    def localtime(cls):
        return cls.day + (12, 0, 0, 0, 0, -1)


class Box:
    def __init__(self, calls):
        self.calls = calls

    def show_all(self):
        self.calls.append("show")


real_localtime = tasks.time.localtime
tasks.time.localtime = Clock.localtime
try:
    win = tasks.Tasks.__new__(tasks.Tasks)
    win._closed = False
    win._cal_day = Clock.day
    calls = []
    win._refresh_calendar = lambda: calls.append("calendar")
    win._populate_events = lambda: calls.append("events")
    win._evbox = Box(calls)
    win._refresh = lambda: calls.append("main")

    check(win._check_day_rollover() is True and calls == [],
          "an unchanged live day keeps polling without rebuilding")
    Clock.day = (2026, 8, 6)
    check(win._check_day_rollover() is True,
          "a live day change keeps the minute poll active")
    check(win._cal_day == Clock.day
          and calls == ["calendar", "events", "show", "main"],
          "a live rollover rebuilds each affected region exactly once")

    win._closed = True
    prior = win._cal_day
    Clock.day = (2026, 8, 7)
    check(win._check_day_rollover() is False,
          "a closed Tasks window drops the repeating callback")
    check(win._cal_day == prior
          and calls == ["calendar", "events", "show", "main"],
          "a closed rollover touches neither state nor widgets")
finally:
    tasks.time.localtime = real_localtime

# Date grouping is recomputed from the real civil date, including month ends;
# completion does not mutate the due date, so a midnight crossing cannot file
# a finished task under a false day.
real_today = tasks._today
try:
    tasks._today = lambda: tasks.date(2026, 2, 28)
    probe = tasks.Tasks.__new__(tasks.Tasks)
    month_end = {"date": "2026-03-01", "due": "tomorrow"}
    check(probe._due_of(month_end) == "tomorrow",
          "month-end tomorrow is grouped by its calendar date")
    tasks._today = lambda: tasks.date(2026, 3, 1)
    check(probe._due_of(month_end) == "today",
          "month-end due date rolls into today at midnight")
    tasks._today = lambda: tasks.date(2026, 3, 2)
    month_end["done"] = True
    check(probe._due_of(month_end) == "overdue",
          "completion across midnight does not falsify the due date")
finally:
    tasks._today = real_today


win = tasks.Tasks.__new__(tasks.Tasks)
win._closed = False
win._day_rollover_id = 91
events = []
win._save_tasks = lambda: events.append(("save", win._closed))
removed = []
real_remove = tasks.GLib.source_remove
tasks.GLib.source_remove = lambda source_id: removed.append(source_id)
try:
    win._on_destroy()
    win._on_destroy()
finally:
    tasks.GLib.source_remove = real_remove

check(win._closed is True, "destroy marks Tasks closed")
check(removed == [91] and win._day_rollover_id == 0,
      "destroy removes exactly its rollover source and clears the ID")
check(events == [("save", True)],
      "final state saves once, after the closed gate is raised")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
