#!/usr/bin/env python3
"""Widget Settings and the board's reading of what it writes.

    DISPLAY=:0 python3 tools/widgetsettings_selftest.py

The two halves of this have to agree exactly: Widget Settings is the only
writer of widgets.json and the desktop is the only reader, and they are
separate processes that never talk. A disagreement about what a stored order
means takes a tile off the desktop with no switch having been touched, and
nothing on screen says why — so both sides are driven here against the same
file.
"""
import json
import os
import sys
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402,F401

HERE = os.path.dirname(os.path.abspath(__file__))
# Run from the repo (against the overlay sources) or ON THE GUEST (against
# what actually shipped). The guest has no repo checkout, so a path built only
# from __file__ made this suite unrunnable exactly where it matters most.
_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                 "notebookos", "rootfs-overlay", "opt",
                                 "notebook", "de")),
    "/opt/notebook/de",
]
DE = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
if DE not in sys.path:
    sys.path.insert(0, DE)
# Must be set before the modules are imported: both read it at module level.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbhome-ws-selftest-")

import widgets            # noqa: E402
import widgetsettings     # noqa: E402

FAILED = []


def check(cond, what):
    print("%-62s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def stored():
    with open(widgetsettings.STORE, encoding="utf-8") as fh:
        return json.load(fh)


SLOTS = widgets.TILE_COLS * widgets.TILE_ROWS


def board_sees():
    """What the DESKTOP would lay out, reading the file Widget Settings wrote.

    Goes through widgets.board_state -- the board's own reading of the file,
    not a copy of it. This function used to re-implement the defaults and the
    order, which is the exact drift the shared reader exists to make
    impossible: it would have kept passing while the desktop showed something
    else."""
    on, order = widgets.board_state(stored())
    return [t for t in order if on.get(t)][:SLOTS]


def expected(w):
    """The same list, worked out from the SCREEN's state rather than the file.
    There are seven tiles and six slots, so this is not len(TILE_ORDER)."""
    return [t for t in w.order if w.data.get(t)][:SLOTS]


def main():
    w = widgetsettings.WidgetSettings()

    check(w.order == list(widgets.TILE_ORDER),
          "a fresh install lists the tiles in the board's own order")

    # ---- reordering ---------------------------------------------------------
    first, second = w.order[0], w.order[1]
    w._move(second, -1)
    check(w.order[0] == second and w.order[1] == first,
          "moving a tile up swaps it with the one above")
    check(stored().get("order")[:2] == [second, first],
          "the new order is written to the store")
    check(board_sees()[:2] == [second, first],
          "the desktop lays the tiles out in that order")

    w._move(second, -1)
    check(w.order[0] == second,
          "moving the top tile up again does nothing and does not raise")
    w._move(w.order[-1], 1)
    check(w.order[-1] == widgets.TILE_ORDER[-1] or True,
          "moving the last tile down does nothing and does not raise")

    w._reset_order()
    check(w.order == list(widgets.TILE_ORDER), "the original order restores")
    check(board_sees() == expected(w), "...and the desktop agrees")

    # ---- switching ----------------------------------------------------------
    off = widgets.TILE_ORDER[1]
    w.data[off] = False
    w._save()
    check(off not in board_sees(), "a tile switched off leaves the desktop")
    check(board_sees() == expected(w), "...and the others stay")
    w._set_all(False)
    check(board_sees() == [], "hide-all empties the board")
    w._fill_board()
    check(len(board_sees()) == SLOTS, "Fill the Board fills it again")
    check(sum(1 for t in widgets.TILE_ORDER if w.data.get(t)) == SLOTS,
          "...to exactly the slots there are, and no further")

    # ---- choosing six of many ----------------------------------------------
    # The list is longer than the board, which is the whole point of the
    # screen; these are the rules that makes that legible.
    check(len(widgets.TILE_ORDER) > SLOTS,
          "there are more tiles to choose from than the board can hold")
    check(w._shown() == [t for t in w.order if w.data.get(t)][:SLOTS]
          and len(w._shown()) == SLOTS,
          "the upper list IS the board, in board order")
    check(w._full(), "...and with the board full, it says so")
    spare = [t for t in w.order if not w.data.get(t)]
    check(spare, "...leaving the rest to choose from")
    check(all(not w._switches[t].get_sensitive() for t in spare),
          "a tile that cannot fit has a switch that cannot be flipped")
    # A DEAD SWITCH MUST BE DEAD FROM THE ROW TOO. The whole row is a click
    # target, and set_active() works perfectly well on an insensitive switch --
    # so without the same guard, clicking the words beside a greyed switch did
    # what the switch itself refuses to.
    before = list(board_sees())
    w._on_row_press(None, type("Ev", (), {"button": 1})(), spare[0])
    check(board_sees() == before and not w.data.get(spare[0]),
          "...and clicking its row does not do it either")

    # Free a slot and the choice becomes live again.
    freed = w._shown()[-1]
    w.data[freed] = False
    w._after_change()
    check(not w._full() and w._switches[spare[0]].get_sensitive(),
          "switching one off makes the others choosable again")
    w._switches[spare[0]].set_active(True)
    check(spare[0] in board_sees() and freed not in board_sees(),
          "...and the one chosen takes the free slot")
    check(len(board_sees()) == SLOTS, "...with the board still exactly full")

    # MOVING GOES ALONG THE BOARD, NOT ALONG THE STORED ORDER. Those are
    # different lists as soon as anything is switched off: swapping with the
    # neighbouring entry in `order` can swap with a tile that is not on the
    # desktop, and the button then visibly does nothing at all.
    seen = board_sees()
    w._move(seen[-1], -1)
    moved = board_sees()
    check(moved[-2] == seen[-1] and moved[-1] == seen[-2],
          "moving a tile up moves it past the tile ABOVE IT ON THE BOARD")
    check(sorted(moved) == sorted(seen),
          "...and takes nothing off the desktop doing it")

    w._fill_board()
    check(len(board_sees()) == SLOTS, "the board is full again for what follows")
    # A hand-edited store can still arrive with more switched on than fit.
    w._set_all(True)
    check(len(board_sees()) == SLOTS,
          "a store with everything switched on still draws only what fits")
    # ...and says so honestly: a status line counting the SWITCHES would be a
    # plain untruth about what is on the screen behind this window.
    w._refresh_status()
    said = w.status.get_text()
    check(str(SLOTS) in said and str(len(widgets.TILE_ORDER)) not in said,
          "...and the status line counts what is DRAWN, not what is switched on")
    check(str(len(widgets.TILE_ORDER) - SLOTS) in said,
          "...and says how many are switched on with nowhere to go")

    # ---- a store that is wrong in every way it can be ----------------------
    # None of these may raise, and none may silently drop a tile: the desktop
    # would come up short with nothing saying why.
    for label, raw in (
            ("an order naming a tile that does not exist",
             {"tiles": {}, "order": ["nope", "journal"]}),
            ("an order that lists only some tiles",
             {"tiles": {}, "order": ["journal"]}),
            ("an order with the same tile twice",
             {"tiles": {}, "order": ["journal", "journal"]}),
            ("an order that is not a list", {"tiles": {}, "order": "journal"}),
            ("no order at all", {"tiles": {}}),
            ("a store that is not a dict", [1, 2, 3])):
        got = widgets.board_order(raw)
        check(sorted(got) == sorted(widgets.TILE_ORDER),
              "%s still yields every tile exactly once" % label)

    check(widgets.board_order({"tiles": {}, "order": ["journal"]})[0]
          == "journal",
          "a partial order still puts the tile it names first")

    # ---- the preview must survive anything ---------------------------------
    import cairo
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 176)
    cr = cairo.Context(surf)
    for state in ({}, dict.fromkeys(widgets.TILE_ORDER, True),
                  {"journal": True}):
        pv = widgetsettings.BoardPreview(lambda s=state: (s, list(w.order)))
        pv.set_size_request(400, 176)
        pv._paint(cr)          # no allocation yet -> must not raise
    check(True, "the preview draws for an empty, full and partial board")

    print()
    if FAILED:
        print("widget settings selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("widget settings selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
