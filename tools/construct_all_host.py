#!/usr/bin/env python3
"""Host-side construct-all: build every app window on the local :0 display to
catch import/construct crashes without a full rebuild+boot. Mirrors
boot-work/construct_all.py but points at the in-tree DE sources."""
import sys, os, importlib, inspect
HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, os.path.abspath(DE))
os.environ.setdefault("NB_HOME", "/tmp/nbhome-construct")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
# Stand clear of the single-instance lock. nbapp.claim_single_instance() exits
# the process with os._exit(0) when the app it is building is already open in
# another process — no traceback, no output, exit status 0. Constructing every
# app in one process means ANY app left running on this desktop (an app being
# screenshotted, an app under test in another window) silently killed this run
# part-way through, and the missing "CONSTRUCT: n ok" line reads like a tool
# that did not run rather than a result. Point the registry at our own
# directory: this process is not a real app and must never stand down.
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)
# The app list is DERIVED from finder.APP_MODULES minus HIDDEN_APPS — the same
# visible set the desktop lets a user launch — so coverage cannot silently
# drift from what a user can actually double-click. APP_MODULES deliberately
# retains withheld/unfinished apps for identity and package bookkeeping; those
# are not part of this stable-surface construct gate. A hardcoded list did
# drift: language/maps/
# gbasdk/gbaemu were all launchable but never launch-crash tested, while the
# summary line still read like full coverage.
import finder as _finder
APPS = sorted({module for name, module in _finder.APP_MODULES.items()
               if name not in _finder.HIDDEN_APPS}) + [
        # Finder itself is not in APP_MODULES (the desktop starts it directly).
        "finder",
        # Desktop / session-start components — NOT apps, but they construct a
        # Gtk.Window at boot and were previously untested, which is how a missing
        # `from nbi18n import _t` shipped a top-panel that crashed on construct.
        "shell","widgets","desktopbg","splash","nbmediakeys",
        # Reached from the desktop's right-click menu rather than the
        # Applications folder, so APP_MODULES does not cover it.
        "widgetsettings"]
ok = fail = 0
for name in APPS:
    try:
        if name in sys.modules: del sys.modules[name]
        m = importlib.import_module(name)
        cls = None
        for _n, c in inspect.getmembers(m, inspect.isclass):
            if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
                cls = c; break
        if cls is None:
            print("NOCLASS %s" % name); continue
        w = cls()
        n = 0
        while Gtk.events_pending() and n < 500:
            Gtk.main_iteration(); n += 1
        try: w.destroy()
        except Exception: pass
        ok += 1
    except Exception as e:
        fail += 1
        import traceback
        print("CRASH   %-12s %s: %s" % (name, type(e).__name__, str(e)[:90]))
print("CONSTRUCT: %d ok, %d crashed" % (ok, fail))
# A terminal verdict the release runner recognises (run_all_gates SUCCESSWORD).
# Without it the aggregate recorded this gate as DID NOT RUN -- the check that
# catches an app crashing on construct was protecting nothing there.
print("RESULT: %s" % ("ALL PASS" if not fail else "FAILED (%d crashed)" % fail))
sys.exit(1 if fail else 0)
