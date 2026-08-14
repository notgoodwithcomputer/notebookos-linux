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

# 9. return continuity: the flag monitor's reappear path must check what is
#    actually RUNNING, not just the flag file. A finished game's exit used to
#    drop the shared app-active flag while the GBA SDK was still open — the
#    Finder's monitor read "screen free" and mapped itself over the IDE
#    (filmed on target, 2.2-consumer). The reappear path now heals a wrongly
#    dropped flag while any app is alive, and only returns when none is.
import subprocess


class _FlagStand(object):
    _sync_app_flag = finder.Finder._sync_app_flag
    _other_apps_running = finder.Finder._other_apps_running

    def __init__(self, visible):
        self.visible = visible
        self.hides = 0
        self.shows = 0
        self.presents = 0

    def get_visible(self):
        return self.visible

    def hide(self):
        self.hides += 1
        self.visible = False

    def show_all(self):
        self.shows += 1
        self.visible = True

    def present(self):
        self.presents += 1

    def _nudge(self):
        return False


def _wait_cmdline(pid, needle, tries=100):
    """Popen's child exists before exec fills /proc/<pid>/cmdline; wait for
    the script path to appear so the scan below cannot race the exec."""
    for _ in range(tries):
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                if needle.encode() in fh.read():
                    return True
        except OSError:
            return False
        time.sleep(0.05)
    return False


fake_de = tempfile.mkdtemp(prefix="finder-launch-fakede-")
sleeper = os.path.join(fake_de, "fakeapp.py")
with open(sleeper, "w") as fh:
    fh.write("import time\ntime.sleep(300)\n")
infra = os.path.join(fake_de, "finder.py")   # an excluded infrastructure name
with open(infra, "w") as fh:
    fh.write("import time\ntime.sleep(300)\n")

real_de_dir = finder.DE_DIR
flag = nbapp.APP_FLAG
finder.DE_DIR = fake_de
procs = []
try:
    try:
        os.remove(flag)
    except OSError:
        pass

    p = subprocess.Popen([sys.executable, sleeper])
    procs.append(p)
    _wait_cmdline(p.pid, sleeper)
    st = _FlagStand(visible=False)
    st._sync_app_flag()
    check("a dropped flag with an app still running is healed, not obeyed",
          os.path.exists(flag))
    check("...and the Finder stays hidden rather than mapping over it",
          st.shows == 0 and st.presents == 0)
    p.terminate()
    p.wait()

    try:
        os.remove(flag)
    except OSError:
        pass
    p2 = subprocess.Popen([sys.executable, infra])
    procs.append(p2)
    _wait_cmdline(p2.pid, infra)
    st = _FlagStand(visible=False)
    st._sync_app_flag()
    check("desktop infrastructure never counts as an app owning the screen",
          st.shows == 1)
    p2.terminate()
    p2.wait()

    try:
        os.remove(flag)
    except OSError:
        pass
    st = _FlagStand(visible=False)
    st._sync_app_flag()
    check("with nothing running the dropped flag returns the Finder",
          st.shows == 1 and st.presents == 1)
    check("returning does not invent a flag", not os.path.exists(flag))

    open(flag, "w").close()
    st = _FlagStand(visible=True)
    st._sync_app_flag()
    check("a fresh flag still hides a visible Finder", st.hides == 1)
    os.remove(flag)
finally:
    finder.DE_DIR = real_de_dir
    for p in procs:
        try:
            p.kill()
            p.wait()
        except Exception:
            pass

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
