#!/usr/bin/env python3
"""Batch-render Notebook OS apps to PNGs for the UI-polish sweep.

Constructs each app the way construct_all_host does (first Gtk.Window subclass
in the module) and renders it offscreen under the real Papertone theme + guest
fonts via uishot.shot_window. Writes <outdir>/<app>.png so each can be Read and
reviewed for artifacts. Non-disruptive (no window is ever mapped on screen).

Usage:
  DISPLAY=:0 FONTCONFIG_FILE=.../target.conf \
  PYTHONPATH=<overlay>/opt/notebook/de:<repo>/tools \
  python3 uishot_all.py <outdir> [app ...]        # default list if none given
"""
import sys
import os
import importlib
import inspect
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import uishot  # noqa: E402

DEFAULT = ["writer", "novel", "journal", "academics", "screenplay", "ebook",
           "cookbook", "contacts", "accounting", "calendar", "music",
           "illustrator", "sequencer", "video", "media", "g2048", "packages",
           "settings", "sysmon", "calculator", "tasks", "language", "maps",
           "finder", "gbasdk"]

W, H = 1000, 680


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="uisweep-")
    apps = sys.argv[2:] or DEFAULT
    os.makedirs(outdir, exist_ok=True)
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="uisweep-home-"))
    uishot.load_theme()
    # Apps that size themselves from the panel (video's preview stage, music's
    # columns, illustrator's zoom-to-fit) must believe they are on the panel we
    # are RENDERING at. Without this they lay out for the developer's monitor
    # and are then squeezed into W x H, which invents defects that do not exist
    # on hardware: the Video Editor's preview came out with its placeholder
    # glyph and "Nothing to preview" line scrolled almost entirely out of a
    # clipped 640px stage, and it renders perfectly at 1024 once asked. Same
    # patch, same reason, as minsize_sweep.measure_one.
    import nbapp
    nbapp.screen_size = lambda: (W, H)
    for name in apps:
        try:
            if name in sys.modules:
                del sys.modules[name]
            m = importlib.import_module(name)
            cls = None
            for _n, c in inspect.getmembers(m, inspect.isclass):
                if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
                    cls = c
                    break
            if cls is None:
                print("NOCLASS %s" % name)
                continue
            win = cls()
            path = os.path.join(outdir, name + ".png")
            uishot.shot_window(win, W, H, path)
            try:
                win.destroy()
            except Exception:
                pass
            print("ok   %-12s %s" % (name, path))
        except Exception as e:
            import traceback
            print("FAIL %-12s %s: %s" % (name, type(e).__name__, str(e)[:80]))
    print("outdir:", outdir)


if __name__ == "__main__":
    main()
