#!/usr/bin/env python3
"""Headless ownership checks for Finder's repeating polls and monitor."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import finder  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


win = finder.Finder.__new__(finder.Finder)
win._closed = False
calls = []
win._sync_app_flag = lambda: calls.append("app")
check(win._poll_app_flag() is True and calls == ["app"],
      "a live fallback app poll reconciles once and continues")

win._mounts_sig = ("old",)
win._devices = lambda: [("USB", "disk", "new")]
# *a/**k, not (): _fill_sidebar takes the newly-arrived mount points (only a new
# volume animates in). A stub pinned to the old zero-argument shape raises
# TypeError inside _poll_devices' except-and-continue guard, so the refresh
# silently stops and this suite blames the poll instead of its own stub.
win._fill_sidebar = lambda *a, **k: calls.append("devices")
check(win._poll_devices() is True and calls[-1] == "devices",
      "a live changed-device poll refreshes once and continues")

win._closed = True
prior = list(calls)
check(win._poll_app_flag() is False and win._poll_devices() is False,
      "closed repeating polls remove themselves")
win._on_app_flag_changed()
check(win._reconcile_app_flag_once() is False and calls == prior,
      "queued monitor and reconcile callbacks ignore a closed Finder")


class Monitor:
    def __init__(self, events, owner):
        self.events = events
        self.owner = owner

    def cancel(self):
        self.events.append(("monitor", self.owner._closed))


class Generation:
    def __init__(self, events, owner):
        self.events = events
        self.owner = owner

    def close(self):
        self.events.append(("generation", self.owner._closed))


win = finder.Finder.__new__(finder.Finder)
win._closed = False
win._dev_poll_id = 11
win._app_poll_id = 12
win._dir_reload_id = 13
events = []
win._app_flag_monitor = Monitor(events, win)
win._dirgen = Generation(events, win)
real_remove = finder.GLib.source_remove
finder.GLib.source_remove = lambda source_id: events.append(
    ("source-%d" % source_id, win._closed))
try:
    win._on_destroy_navigation()
    win._on_destroy_navigation()
finally:
    finder.GLib.source_remove = real_remove

check(win._closed is True, "destroy marks Finder closed first")
check([name for name, _closed in events]
      == ["source-11", "source-12", "monitor", "generation", "source-13"],
      "destroy releases each owned source and monitor exactly once")
check(all(closed for _name, closed in events),
      "every teardown action observes the closed gate already raised")
check(win._dev_poll_id == win._app_poll_id == win._dir_reload_id == 0
      and win._app_flag_monitor is None,
      "destroy clears every released source and monitor field")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
