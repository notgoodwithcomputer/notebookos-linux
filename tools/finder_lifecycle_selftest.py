#!/usr/bin/env python3
"""Display-free acceptance checks for the Finder's repeating-poll lifecycle.

A Finder window schedules two repeating sources that outlive nothing on their
own: a 5s Devices re-read (so a USB stick inserted later shows up) and — only
where no file-monitor backend exists — a 3s poll of the app-active flag. Both
callbacks used to return True unconditionally and neither source id was kept,
so closing a Finder left them running: the callbacks went on rebuilding the
sidebar and calling show_all()/hide() on a destroyed window, and because GLib
holds a reference to the bound method, the whole window stayed retained. The
Gio file monitor was never cancelled either.

None of that is visible in a screenshot — a leaked poll looks exactly like a
closed window — so it is checked here instead, against the real methods.

The window is built with Finder.__new__: no Gtk, no display, no filesystem.
Only the lifecycle fields the methods read are filled in, and GLib is swapped
for a recorder, so what these checks see is the scheduling and teardown itself.

Run as:
  python3 tools/finder_lifecycle_selftest.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import finder  # noqa: E402

FAILED = []


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILED.append(label)


class FakeGLib:
    """Records what was scheduled and what was removed."""

    def __init__(self):
        self.next_id = 100
        self.added = []          # (interval, callback)
        self.removed = []        # source ids passed to source_remove
        self.raise_on_remove = False

    def timeout_add_seconds(self, interval, cb, *a):
        self.next_id += 1
        self.added.append((interval, cb))
        return self.next_id

    timeout_add = timeout_add_seconds

    def source_remove(self, sid):
        self.removed.append(sid)
        if self.raise_on_remove:
            raise ValueError("no such source")
        return True


class FakeGen:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


class FakeMonitor:
    def __init__(self):
        self.cancels = 0

    def cancel(self):
        self.cancels += 1


def window(glib, closed=False):
    """A Finder with only its lifecycle state — never a Gtk widget."""
    w = finder.Finder.__new__(finder.Finder)
    w._closed = closed
    w._dev_poll_id = 0
    w._app_poll_id = 0
    w._app_flag_monitor = None
    w._dirgen = FakeGen()
    w._dir_reload_id = 0
    w.calls = []
    # The two things a poll may reach for. Recording them is the point: a
    # closed window must touch neither.
    # *a/**k, not (): _fill_sidebar takes the set of newly-arrived mount points
    # so only a new volume animates in. A stub pinned to the old zero-argument
    # shape raises TypeError inside _poll_devices' except-and-continue guard,
    # so the refresh silently stops happening and this suite reports it as the
    # POLL being broken rather than as its own stub being stale.
    w._fill_sidebar = lambda *a, **k: w.calls.append("fill_sidebar")
    w._devices = lambda: (w.calls.append("devices") or
                          [("d", "disk", "/mnt/stick")])
    w._sync_app_flag = lambda: w.calls.append("sync_app_flag")
    w._mounts_sig = None
    return w


def live_polls_run_and_repeat(glib):
    w = window(glib)
    check(w._poll_devices() is True,
          "live device poll asks to be called again")
    check(w.calls == ["devices", "fill_sidebar"],
          "live device poll re-reads mounts and rebuilds the sidebar")

    w2 = window(glib)
    check(w2._poll_app_flag() is True,
          "live app-flag poll asks to be called again")
    check(w2.calls == ["sync_app_flag"],
          "live app-flag poll reconciles visibility")


def closed_polls_stop_and_touch_nothing(glib):
    w = window(glib, closed=True)
    check(w._poll_devices() is False,
          "closed device poll returns False so GLib drops the source")
    check(w._poll_app_flag() is False,
          "closed app-flag poll returns False so GLib drops the source")
    check(w.calls == [],
          "a closed window's polls touch no sidebar and no visibility")


def event_paths_ignore_a_closed_window(glib):
    w = window(glib, closed=True)
    w._on_app_flag_changed(None, None, None, 0)
    check(w.calls == [],
          "a queued monitor event on a closed window changes nothing")
    check(w._reconcile_app_flag_once() is False,
          "the one-shot reconcile never repeats")
    check(w.calls == [],
          "the one-shot reconcile ignores a closed window")

    live = window(glib)
    live._on_app_flag_changed(None, None, None, 0)
    live._reconcile_app_flag_once()
    check(live.calls == ["sync_app_flag", "sync_app_flag"],
          "both event paths still reconcile a live window")


def destroy_releases_everything(glib):
    w = window(glib)
    w._dev_poll_id = 11
    w._app_poll_id = 22
    w._dir_reload_id = 33
    mon = FakeMonitor()
    w._app_flag_monitor = mon
    # A poll racing the teardown must already see a closed window: record what
    # _closed looked like at the moment the first source was removed.
    seen = []
    real_remove = glib.source_remove
    glib.source_remove = lambda sid: (seen.append(w._closed) or
                                      real_remove(sid))
    w._on_destroy_navigation()
    glib.source_remove = real_remove

    check(w._closed is True, "destroy marks the window closed")
    check(seen and all(seen), "closed is set BEFORE any source is removed")
    check(glib.removed == [11, 22, 33],
          "destroy removes exactly the recorded sources: %r" % (glib.removed,))
    check(mon.cancels == 1, "destroy cancels the file monitor once")
    check(w._dirgen.closes == 1, "destroy closes the directory generation")
    check((w._dev_poll_id, w._app_poll_id, w._dir_reload_id) == (0, 0, 0),
          "destroy clears every source field")
    check(w._app_flag_monitor is None, "destroy clears the monitor field")

    del glib.removed[:]
    w._on_destroy_navigation()
    check(glib.removed == [] and mon.cancels == 1 and w._dirgen.closes == 1,
          "a second destroy is harmless: nothing is removed or cancelled twice")


def teardown_survives_a_dead_source(glib):
    w = window(glib)
    w._dev_poll_id = 44
    glib.raise_on_remove = True
    try:
        w._on_destroy_navigation()
        raised = False
    except Exception:
        raised = True
    glib.raise_on_remove = False
    check(not raised, "destroy survives a source that GLib already dropped")
    check(w._dev_poll_id == 0,
          "a source that failed to remove is still cleared")


def sidebar_does_not_stack_pollers(glib):
    w = window(glib)
    w._dev_poll_id = 55
    w._stop_source("_dev_poll_id")
    w._dev_poll_id = finder.GLib.timeout_add_seconds(5, w._poll_devices)
    check(glib.removed[-1] == 55,
          "rebuilding the sidebar removes the previous device poll")
    check(w._dev_poll_id != 0, "...and records the new one")
    check(glib.added[-1][0] == 5, "the 5s device cadence is unchanged")

    w2 = window(glib)
    w2._app_poll_id = finder.GLib.timeout_add_seconds(3, w2._poll_app_flag)
    check(glib.added[-1][0] == 3, "the 3s fallback cadence is unchanged")


if __name__ == "__main__":
    fake = FakeGLib()
    real_glib = finder.GLib
    finder.GLib = fake
    try:
        live_polls_run_and_repeat(fake)
        closed_polls_stop_and_touch_nothing(fake)
        event_paths_ignore_a_closed_window(fake)
        destroy_releases_everything(fake)
        del fake.removed[:]
        teardown_survives_a_dead_source(fake)
        del fake.removed[:]
        sidebar_does_not_stack_pollers(fake)
    finally:
        finder.GLib = real_glib
    if FAILED:
        print("\nFinder lifecycle selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        sys.exit(1)
    print("\nFinder lifecycle selftest: OK")
