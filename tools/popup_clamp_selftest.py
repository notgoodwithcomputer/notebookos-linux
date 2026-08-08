#!/usr/bin/env python3
"""popup_clamp_selftest — nbapp.popup_at's clamp keeps menus on screen.

The clamp is pure math, tested exhaustively at the edges; popup_at itself is
verified for shape (signature, returns the menu, wraps the two GTK popup
calls) by reading the source, the way commands_selftest checks nbapp.
Exit status is the number of failures.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="popup-clamp-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import nbapp                                                  # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


WORK = (0, 46, 1024, 722)          # the real panel-strut workarea at 768

# inside: identity
check("a menu inside the area does not move",
      nbapp.clamp_to_work(300, 300, 200, 150, WORK) == (300, 300))
# each escape direction
check("right escape clamps to the right edge",
      nbapp.clamp_to_work(900, 300, 200, 150, WORK) == (824, 300))
check("bottom escape clamps to the bottom edge",
      nbapp.clamp_to_work(300, 700, 200, 150, WORK) == (300, 618))
check("left escape clamps to the left edge",
      nbapp.clamp_to_work(-40, 300, 200, 150, WORK) == (0, 300))
check("a menu may never sit above the panel strut",
      nbapp.clamp_to_work(300, 10, 200, 150, WORK) == (300, 46))
check("the bottom-right corner clamps both axes",
      nbapp.clamp_to_work(1000, 760, 200, 150, WORK) == (824, 618))
# degenerate: taller than the area — the top-left must survive
check("an oversized menu pins to the area origin",
      nbapp.clamp_to_work(500, 500, 1400, 900, WORK) == (0, 46))

src = inspect.getsource(nbapp.popup_at)
check("popup_at wraps both GTK popup shapes",
      "popup_at_widget" in src and "popup_at_pointer" in src)
check("verification runs against the workarea minus the strut",
      "get_workarea" in src and "PANEL_H" in src)
check("the map hook is one-shot", "disconnect" in src)
check("popup_at returns the menu", src.rstrip().endswith("return menu"))

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
