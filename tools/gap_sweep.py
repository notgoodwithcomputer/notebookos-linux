#!/usr/bin/env python3
"""gap_sweep — detect the "floating bottom bar" bug class at several screen sizes.

GTK3 propagates vexpand UP from descendants: a fixed bottom bar containing a
Gtk.Scale (a seek slider, a volume slider, a timeline) becomes an expanding
child, swallows the column's vertical slack, and ends up floating in the middle
of the window with dead paper above and below it. That is the bug a user
reported in Music.

This allocates each app at a given size and measures the gap between the bottom
of its last visible content child and the bottom of the content area. A gap of
more than a few pixels means something is not sitting where it should.

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
    python3 tools/gap_sweep.py OUTDIR [WxH ...]
"""
import os
import sys
import importlib
import inspect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import uishot  # noqa: E402

APPS = ["academic", "calculator", "accounting", "calendar", "cookbook",
        "contacts", "g2048", "ebook", "gbaemu", "illustrator", "media",
        "journal", "maps", "gbaide", "packages", "language", "novel",
        "screenplay", "settings", "sysmon", "tasks", "writer", "music",
        "video", "sequencer", "terminal"]

out = sys.argv[1]
sizes = [tuple(int(v) for v in s.split("x")) for s in sys.argv[2:]] or \
    [(1024, 740), (1366, 740), (1920, 1052)]
os.makedirs(out, exist_ok=True)
os.environ.setdefault("NB_HOME", os.path.join(out, "nbhome"))
os.makedirs(os.environ["NB_HOME"], exist_ok=True)
uishot.load_theme()

for (W, H) in sizes:
    print("\n=== %dx%d ===" % (W, H))
    for name in APPS:
        try:
            if name in sys.modules:
                del sys.modules[name]
            m = importlib.import_module(name)
            cls = None
            for _n, c in inspect.getmembers(m, inspect.isclass):
                if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
                    cls = c
                    break
            app = cls()
            child = app.get_child()
            app.remove(child)
            off = Gtk.OffscreenWindow()
            off.set_size_request(W, H)
            off.add(child)
            off.show_all()
            for _ in range(80):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            cont = app.content
            kids = [k for k in cont.get_children() if k.get_visible()]
            ca = cont.get_allocation()
            if not kids:
                print("  %-12s no visible content children" % name)
            else:
                last = kids[-1].get_allocation()
                gap = ca.y + ca.height - (last.y + last.height)
                flag = "   <<< GAP %dpx" % gap if gap > 6 else ""
                print("  %-12s content_h=%-5d last_bottom=%-5d%s"
                      % (name, ca.height, last.y + last.height, flag))
            app.destroy()
        except Exception as e:
            print("  %-12s ERR %s" % (name, str(e)[:60]))
