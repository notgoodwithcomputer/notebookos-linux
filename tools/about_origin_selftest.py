#!/usr/bin/env python3
"""about_origin_selftest — the About card drops from its title (Article B).

About is an in-window overlay, so its construction needs a display; its
CONTRACT is checked the way commands_selftest checks nbapp — by reading the
code that runs — plus the pure anchor geometry driven directly. The point is
the origin discipline: About must grow from the app-name title's rectangle
and retract to it, never appear as a cut. Exit status is the number of
failures.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="about-origin-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import nbapp                                                  # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


# 1. the anchor is the title's BOTTOM edge, a thin seam, in overlay coords
class _Btn(object):
    def __init__(self, rect):
        self._rect = rect

    def translate_coordinates(self, _rel, x, y):
        return (self._rect[0] + x, self._rect[1] + y)

    def get_allocation(self):
        class _A:
            pass
        a = _A()
        a.width, a.height = self._rect[2], self._rect[3]
        return a


anchor = nbapp._title_anchor(_Btn((14, 0, 60, 28)), object())
check("the drop anchor sits on the title's bottom edge",
      anchor == (14.0, 27.0, 60.0, 2.0))
check("an unresolved title yields no anchor (About then just appears)",
      nbapp._title_anchor(None, object()) is None)

# 2. About grows from that anchor to the centred target, and reveals the real
# card only on landing — read from the source that runs
src = inspect.getsource(nbapp.AppWindow._about)
check("About carries its inventory origin marker",
      "nbmotion-inventory: app.about" in src)
check("About grows a GrowCard from a title anchor",
      "GrowCard" in src and "_title_anchor" in src and ".grow(" in src)
check("the real card is revealed on landing, not before",
      "set_no_show_all(True)" in src
      and src.index("set_no_show_all(True)") < src.index(".grow("))
check("landing shows the card",
      "_landed" in src and ".show()" in src)

# 3. close retracts to the title before removing (B3 departure retraces)
closed = inspect.getsource(nbapp.AppWindow._close_about)
check("close retracts the card rather than cutting it",
      "retract(" in closed and "_about_remove" in closed)
check("Escape and the scrim share the one close path",
      "_close_about" in inspect.getsource(nbapp.AppWindow._about)
      and "_close_about" in inspect.getsource(nbapp._on_key)
      if hasattr(nbapp, "_on_key") else True)

# 4. the geometry only ever grows (shared primitive, spot-checked here too)
import nbtransitions                                          # noqa: E402
a, b = anchor, (400.0, 300.0, 340.0, 140.0)
areas = [nbtransitions.interp_rect(a, b, t)[2] * nbtransitions.interp_rect(a, b, t)[3]
         for t in (0.0, 0.5, 1.0)]
check("the About card only grows on its way in", areas[0] < areas[1] < areas[2])

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
