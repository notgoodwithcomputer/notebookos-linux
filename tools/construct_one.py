#!/usr/bin/env python3
"""Construct ONE app window on the local display to catch import/construct
crashes fast and in isolation (windows are never shown). Usage:
    python3 tools/construct_one.py <appname>
Exits non-zero and prints CRASH ... on failure."""
import sys, os, importlib, inspect
HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
      "notebookos", "rootfs-overlay", "opt", "notebook", "de"))
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/tmp/nbhome-construct")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
name = sys.argv[1]
try:
    m = importlib.import_module(name)
    cls = None
    for _n, c in inspect.getmembers(m, inspect.isclass):
        if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
            cls = c; break
    if cls is None:
        print("NOCLASS %s" % name); sys.exit(2)
    w = cls()
    n = 0
    while Gtk.events_pending() and n < 800:
        Gtk.main_iteration(); n += 1
    try: w.destroy()
    except Exception: pass
    print("OK %s constructs" % name)
except Exception as e:
    import traceback; traceback.print_exc()
    print("CRASH %s %s: %s" % (name, type(e).__name__, str(e)[:100]))
    sys.exit(1)
