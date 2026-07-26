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

DEFAULT = ["writer", "novel", "journal", "academic", "screenplay", "ebook",
           "cookbook", "contacts", "accounting", "calendar", "music",
           "illustrator", "sequencer", "video", "media", "g2048", "packages",
           "settings", "sysmon", "calculator", "tasks", "language", "maps",
           "finder", "gbaide"]

W, H = 1000, 680


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="uisweep-")
    apps = sys.argv[2:] or DEFAULT
    os.makedirs(outdir, exist_ok=True)
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="uisweep-home-"))
    uishot.load_theme()
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
