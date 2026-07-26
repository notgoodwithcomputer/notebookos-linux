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
APPS = ["writer","novel","journal","academic","screenplay","ebook","cookbook",
        "contacts","accounting","calendar","music","illustrator",
        "sequencer","video","media","g2048","packages","settings",
        "sysmon","calculator","terminal","tasks","installer",
        # Desktop / session-start components — NOT apps, but they construct a
        # Gtk.Window at boot and were previously untested, which is how a missing
        # `from nbi18n import _t` shipped a top-panel that crashed on construct.
        "shell","widgets","desktopbg","splash","nbmediakeys"]
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
sys.exit(1 if fail else 0)
