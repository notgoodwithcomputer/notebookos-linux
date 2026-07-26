#!/usr/bin/env python3
"""Capture one app window (in the current $NB_LANG) to a PNG, for visual i18n
proof. Usage: i18n_shot.py APP OUT.png   (run under Xvfb)."""
import sys
import os
import importlib
import inspect
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, os.path.abspath(DE))
os.environ.setdefault("NB_HOME", "/tmp/nbhome-shot")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

name, out = sys.argv[1], sys.argv[2]
m = importlib.import_module(name)
cls = None
for _n, c in inspect.getmembers(m, inspect.isclass):
    if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
        cls = c
        break
w = cls()
w.show_all()
for _ in range(400):
    while Gtk.events_pending():
        Gtk.main_iteration()
    time.sleep(0.005)
while Gtk.events_pending():
    Gtk.main_iteration()
gw = w.get_window()
pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
pb.savev(out, "png", [], [])
print("saved", out, gw.get_width(), "x", gw.get_height())
