#!/usr/bin/env python3
"""Headless ownership checks for Journal's deferred UI/save callbacks."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import journal  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Search:
    def get_text(self):
        return "  final query  "


win = journal.Journal.__new__(journal.Journal)
win._closed = False
win._filter_timer = 11
win.search = Search()
calls = []
win._save_current = lambda: calls.append("save-current")
win._refresh_list = lambda: calls.append("refresh-list")
check(win._filter_tick() is False and win._filter_timer is None
      and win._query == "final query"
      and calls == ["save-current", "refresh-list"],
      "a live filter sink flushes and rebuilds once, then unregisters")

win._count_timer = 12
win._recount = lambda: calls.append("recount")
check(win._recount_tick() is False and win._count_timer is None
      and calls[-1] == "recount",
      "a live recount sink runs once and unregisters")

win._save_timer = 13
win.entries = []
win.active = -1
win._persist = lambda: calls.append("persist") or False
win._sync_active_row = lambda: calls.append("sync-row")
check(win._did_save() is False and win._save_timer is None
      and calls[-3:] == ["save-current", "persist", "sync-row"],
      "a live autosave sink writes and synchronizes once")

win._closed = True
before = list(calls)
for attr, callback in (("_filter_timer", win._filter_tick),
                       ("_count_timer", win._recount_tick),
                       ("_save_timer", win._did_save)):
    setattr(win, attr, 99)
    check(callback() is False and getattr(win, attr) is None,
          "a closed %s sink unregisters" % attr)
check(calls == before, "closed sinks touch no model, disk, or widgets")

win._loading = False
win._count_timer = None
win._save_timer = None
win._on_change(None)
check(win._count_timer is None and win._save_timer is None,
      "editing signals after close arm no new callbacks")


class Undo:
    def __init__(self, events, owner):
        self.events = events
        self.owner = owner

    def cancel(self):
        self.events.append(("undo", self.owner._closed))


win = journal.Journal.__new__(journal.Journal)
win._closed = False
win._save_timer = 21
win._count_timer = 22
win._filter_timer = 23
events = []
win.undo = Undo(events, win)
win._save_current = lambda: events.append(("save-current", win._closed))
win._persist = lambda: events.append(("persist", win._closed))
real_remove = journal.GLib.source_remove
journal.GLib.source_remove = lambda source_id: events.append(
    ("source-%d" % source_id, win._closed))
try:
    win._on_destroy()
    win._on_destroy()
finally:
    journal.GLib.source_remove = real_remove

check(win._closed is True, "destroy marks Journal closed first")
check([name for name, _closed in events]
      == ["undo", "source-21", "source-22", "source-23",
          "save-current", "persist"],
      "destroy cancels callbacks then performs one final flush in order")
check(all(closed for _name, closed in events),
      "every cancellation and final write observes the closed gate")
check(win._save_timer is None and win._count_timer is None
      and win._filter_timer is None,
      "destroy clears all deferred source IDs")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
