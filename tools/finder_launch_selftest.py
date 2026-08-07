#!/usr/bin/env python3
"""finder_launch_selftest — launch continuity (PAPER-PHYSICS G1).

The Finder must never hide before the launched app's first map: the old
_launch_module hid immediately after Popen, so on the software path every
launch was a blank desktop for a second — and a launch that CRASHED before
mapping was a blank desktop until the child-watch fired. The contract now:

  - nbapp writes <pid>.mapped in the shared app dir on the app's first map;
  - the Finder polls that beacon and steps aside only when it appears;
  - a process that dies unmapped leaves the Finder in place, with a message;
  - a process that runs but never maps falls back to the old hide at a
    deadline (never worse than before);
  - the beacon is reaped with its pid marker once the process is gone.

Widget-free: the watch is driven as a plain method over a stand-in, the
beacon writer over a stub window, the reaper over a temp directory.
Exit status is the number of failures.
"""
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="finder-launch-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import nbapp                                                  # noqa: E402
import finder                                                 # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


class _Stand(object):
    _launch_watch = finder.Finder._launch_watch

    def __init__(self, beacon, pid, deadline):
        self._launch_beacon = beacon
        self._launch_pid = pid
        self._launch_deadline = deadline
        self.stepped = 0
        self.retracted = 0
        self.flashed = []

    def _step_aside(self):
        self.stepped += 1

    def _zoom_retract(self):
        self.retracted += 1

    def _flash_status(self, msg, restore_ms=2400):
        self.flashed.append(msg)


tmp = tempfile.mkdtemp(prefix="finder-launch-beacon-")
beacon = os.path.join(tmp, "%d.mapped" % os.getpid())

# 1. beacon present -> step aside, stop polling
open(beacon, "w").close()
st = _Stand(beacon, os.getpid(), time.monotonic() + 8)
check("beacon present: steps aside and stops",
      st._launch_watch() is False and st.stepped == 1 and not st.flashed)
os.remove(beacon)

# 2. process died before mapping -> stay put, retract the card, say so
st = _Stand(beacon, 99999999, time.monotonic() + 8)
check("dead unmapped launch: never hides, retracts, says so",
      st._launch_watch() is False and st.stepped == 0
      and st.retracted == 1 and len(st.flashed) == 1)

# 3. alive, unmapped, before the deadline -> keep polling
st = _Stand(beacon, os.getpid(), time.monotonic() + 8)
check("alive and early: keeps polling",
      st._launch_watch() is True and st.stepped == 0 and not st.flashed)

# 4. alive but never maps -> the old hide at the deadline, never worse
st = _Stand(beacon, os.getpid(), time.monotonic() - 1)
check("deadline passed: falls back to the old hide",
      st._launch_watch() is False and st.stepped == 1)

# 5. a cleared pid means a dead watcher, whatever else is true
st = _Stand(beacon, None, time.monotonic() + 8)
check("cleared pid: watcher is inert", st._launch_watch() is False
      and st.stepped == 0 and not st.flashed)

# 6. the beacon writer: once per window, and only once
appdir = tempfile.mkdtemp(prefix="finder-launch-appdir-")
real_dir = nbapp._APP_DIR
nbapp._APP_DIR = appdir
try:
    class _Win(object):
        _assert_fullscreen = nbapp.AppWindow._assert_fullscreen

        def __init__(self):
            self.fullscreens = 0

        def fullscreen(self):
            self.fullscreens += 1

    w = _Win()
    w._assert_fullscreen()
    mine = os.path.join(appdir, "%d.mapped" % os.getpid())
    check("first map writes the beacon", os.path.exists(mine))
    before = os.stat(mine).st_mtime_ns
    w._assert_fullscreen()
    check("later maps do not rewrite it",
          os.stat(mine).st_mtime_ns == before and w.fullscreens == 2)

    # 7. the reaper: a dead pid's beacon goes, a live one's stays
    open(os.path.join(appdir, "99999999.mapped"), "w").close()
    open(os.path.join(appdir, str(os.getpid())), "w").close()
    nbapp._refresh_app_flag()
    check("reaper removes a dead pid's beacon",
          not os.path.exists(os.path.join(appdir, "99999999.mapped")))
    check("reaper keeps a live pid's beacon", os.path.exists(mine))
finally:
    nbapp._APP_DIR = real_dir

# 8. the launch card's geometry: exact endpoints, monotone growth
class _Zoom(object):
    _zoom_rect = finder.Finder._zoom_rect

    def __init__(self):
        self._zoom_from = (100.0, 200.0, 40.0, 30.0)
        self._zoom_to = (0.0, 0.0, 1024.0, 722.0)


z = _Zoom()
check("card starts exactly on the icon", z._zoom_rect(0.0) == z._zoom_from)
check("card ends exactly on the window", z._zoom_rect(1.0) == z._zoom_to)
areas = [z._zoom_rect(t)[2] * z._zoom_rect(t)[3]
         for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
check("the card only ever grows", all(b > a for a, b in zip(areas, areas[1:])))

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
