#!/usr/bin/env python3
"""finder_info_card_selftest — Get Info grows from its row (Article B, G2).

Get Info stopped being a modal dialog that appears from nowhere and became a
card that grows from the selected row and retracts to it. Construction needs
a display, so the CONTRACT is read from the source (commands_selftest's way)
and the reusable presenter's shape is asserted. Exit status is the failures.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="finder-info-card-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                 # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


# 1. Get Info carries the inventory marker and grows from the row anchor
gi_src = inspect.getsource(finder.Finder._get_info)
check("Get Info carries its inventory origin marker",
      "nbmotion-inventory: finder.get-info" in gi_src)
check("Get Info captures the selected row as the anchor",
      "_selected_row_anchor()" in gi_src and "anchor)" in
      inspect.getsource(finder.Finder._show_info_dialog))

# 2. the anchor is the row rect via the shared cell-origin helper
anc = inspect.getsource(finder.Finder._selected_row_anchor)
check("the row anchor uses the cell-origin geometry",
      "_cell_origin_tree" in anc and "get_column(0)" in anc)
check("grid view / no selection yields no anchor (centre-grows)",
      "return None" in anc)

# 3. the presenter: grows from the anchor, reveals on landing, retracts on close
pres = inspect.getsource(finder.Finder._present_card_from)
check("presenter grows a GrowCard from the anchor",
      "GrowCard" in pres and ".grow(anchor" in pres)
check("presenter reveals the real content only on landing",
      "set_no_show_all(True)" in pres
      and pres.index("set_no_show_all(True)") < pres.index(".grow(anchor"))
check("close retracts before removing (B3 departure retraces)",
      "retract(" in pres and "remove()" in pres)
check("the returned handle emits destroy (async fill watch unchanged)",
      "card_win.destroy()" in pres and pres.rstrip().endswith("return card_win"))
check("scrim click and Esc both route through the one close path",
      "_info_close" in pres
      and "_info_close" in inspect.getsource(finder.Finder._info_key))

# 4. Esc leaves, never destroys the file — the OS-wide contract
key = inspect.getsource(finder.Finder._info_key)
check("Esc routes through close, does not destroy anything else",
      "_info_close()" in key and "os.remove" not in key and "rmtree" not in key)

# 5. the geometry only grows (shared primitive, spot-checked)
import nbtransitions                                          # noqa: E402
a, b = (200.0, 300.0, 180.0, 24.0), (342.0, 250.0, 340.0, 220.0)
areas = [nbtransitions.interp_rect(a, b, t)[2] * nbtransitions.interp_rect(a, b, t)[3]
         for t in (0.0, 0.5, 1.0)]
check("the info card only grows on its way in", areas[0] < areas[1] < areas[2])

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
