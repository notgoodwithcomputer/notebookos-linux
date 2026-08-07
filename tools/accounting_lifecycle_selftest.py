#!/usr/bin/env python3
"""Headless ownership checks for Accounting's deferred search callback."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import accounting  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


win = accounting.Accounting.__new__(accounting.Accounting)
win._closed = False
win._search_timer = 11
win._shown = 99
events = []
win._refresh = lambda: events.append("refresh")
check(win._search_timeout() is False, "a live search sink unregisters")
check(win._search_timer == 0 and win._shown == win._PAGE,
      "a live search sink resets paging")
check(events == ["refresh"], "a live search sink refreshes exactly once")

win._closed = True
win._search_timer = 12
win._shown = 77
check(win._search_timeout() is False and win._search_timer == 0,
      "a closed dispatched search sink unregisters")
check(win._shown == 77 and events == ["refresh"],
      "a closed search sink touches no model or widgets")


class UnreadableEntry:
    def get_text(self):
        raise AssertionError("closed search read its entry")


win.filter = "existing"
win._terms = ("existing",)
win._search_timer = 31
real_timeout_add = accounting.GLib.timeout_add
accounting.GLib.timeout_add = lambda *_args: events.append("scheduled") or 32
try:
    check(win._on_search(UnreadableEntry()) is None,
          "a closed search signal returns immediately")
finally:
    accounting.GLib.timeout_add = real_timeout_add
check((win.filter, win._terms, win._search_timer)
      == ("existing", ("existing",), 31) and "scheduled" not in events,
      "a closed search signal neither mutates nor schedules")

win = accounting.Accounting.__new__(accounting.Accounting)
win._closed = False
win._search_timer = 41
events = []
win._autosave = lambda: events.append(("autosave", win._closed))
real_remove = accounting.GLib.source_remove
accounting.GLib.source_remove = lambda source_id: events.append(
    ("source-%d" % source_id, win._closed))
try:
    first = win._on_destroy()
    second = win._on_destroy()
finally:
    accounting.GLib.source_remove = real_remove

check(first is False and second is False, "destroy always returns False")
check(win._closed is True, "destroy marks Accounting closed first")
check(events == [("source-41", True), ("autosave", True)],
      "destroy cancels then performs exactly one final save behind the gate")
check(win._search_timer == 0, "destroy clears search ownership")

print("\n%d checks, %d failed" % (checks, len(failures)))
sys.exit(1 if failures else 0)
