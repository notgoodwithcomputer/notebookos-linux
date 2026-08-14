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
import nbtransitions                                          # noqa: E402

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

# 2. About now DELEGATES to the shared nbtransitions.present_card (extracted
# 2026-08-08); present_card_selftest gates the grow / reveal-on-landing / retract
# in full. Here: About carries its marker, drops from the TITLE anchor, and
# routes through the shared presenter -- where the motion actually lives.
src = inspect.getsource(nbapp.AppWindow._about)
check("About carries its inventory origin marker",
      "nbmotion-inventory: app.about" in src)
check("About delegates to nbtransitions.present_card from a title anchor",
      "present_card(" in src and "_title_anchor" in src)
shared = inspect.getsource(nbtransitions.present_card)
check("the shared presenter grows a GrowCard from the anchor",
      "GrowCard" in shared and ".grow(anchor" in shared)
check("the real card is revealed on landing, not before",
      "set_no_show_all(True)" in shared
      and shared.index("set_no_show_all(True)") < shared.index(".grow(anchor"))
check("landing shows the card",
      "reveal" in shared and "card_win.show()" in shared)

# 3. close retracts to the title before removing (B3 departure retraces)
closed = inspect.getsource(nbapp.AppWindow._close_about)
check("close routes through the shared presenter's retract-then-remove",
      "close()" in closed and "retract(" in shared and "remove()" in shared)
check("Escape and the scrim share the one close path",
      "present_card(" in src
      and "_close_about" in inspect.getsource(nbapp.AppWindow._on_key))

# 4. the geometry only ever grows (shared primitive, spot-checked here too)
import nbtransitions                                          # noqa: E402
a, b = anchor, (400.0, 300.0, 340.0, 140.0)
areas = [nbtransitions.interp_rect(a, b, t)[2] * nbtransitions.interp_rect(a, b, t)[3]
         for t in (0.0, 0.5, 1.0)]
check("the About card only grows on its way in", areas[0] < areas[1] < areas[2])

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
