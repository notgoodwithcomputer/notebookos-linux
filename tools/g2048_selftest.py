#!/usr/bin/env python3
"""g2048_selftest — the board must repaint only the tiles that changed.

    python3 tools/g2048_selftest.py        (no display needed)

2048's _refresh() runs on every keypress, and the arrow keys auto-repeat while
held. Setting a label's text or swapping its style class invalidates that
widget's style and queues a resize of the whole board, so a _refresh that walks
all 16 tiles unconditionally pays 16 restyles per move — on this
software-rendered, compositor-less stack that wasted relayout is exactly what
makes the board feel like it lags behind the key. A move changes at most a
handful of cells, and an idle refresh (dismissing the win banner, closing a
menu) changes none.

This is a static, widget-free check: _refresh is driven as a plain function
over stand-in labels that count every mutation, so it needs neither an X
display nor a real GtkWindow. Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="g2048-selftest-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import g2048                                                  # noqa: E402

FAILS = []


class _Ctx(object):
    """A style context that records class churn instead of restyling."""

    def __init__(self, label):
        self.label = label
        self.classes = {"tile"}

    def list_classes(self):
        return sorted(self.classes)

    def add_class(self, cls):
        self.classes.add(cls)
        self.label.ops += 1

    def remove_class(self, cls):
        self.classes.discard(cls)
        self.label.ops += 1


class _Label(object):
    """Counts every state-invalidating call made against it."""

    def __init__(self):
        self.text = ""
        self.ops = 0
        self.ctx = _Ctx(self)

    def get_style_context(self):
        return self.ctx

    def set_text(self, text):
        self.text = text
        self.ops += 1


class _Widget(object):
    def show(self):
        pass

    def hide(self):
        pass

    def set_text(self, _text):
        pass


class _Board(object):
    """Game2048._refresh with the window taken away."""

    _refresh = g2048.Game2048._refresh

    def __init__(self, board, score=0, best=0, status="play"):
        self.board = [list(r) for r in board]
        self.score = score
        self.best = best
        self.status = status
        self.tiles = [[_Label() for _ in range(4)] for _ in range(4)]
        self.score_lbl = _Label()
        self.best_lbl = _Label()
        self._cell_state = [[None] * 4 for _ in range(4)]
        self._score_shown = None
        self._best_shown = None
        self.ov_box = _Widget()
        self.ov_text = _Widget()
        self.keep_btn = _Widget()

    def zero(self):
        for row in self.tiles:
            for lbl in row:
                lbl.ops = 0
        self.score_lbl.ops = self.best_lbl.ops = 0

    def touched(self):
        return {(r, c) for r in range(4) for c in range(4)
                if self.tiles[r][c].ops}

    def ops(self):
        return sum(lbl.ops for row in self.tiles for lbl in row)


def check(name, ok):
    if not ok:
        FAILS.append(name)
    print("%-46s %s" % (name, "ok" if ok else "FAIL"))


START = [[2, 0, 0, 0],
         [0, 4, 0, 0],
         [0, 0, 0, 0],
         [8, 0, 0, 16]]

# 1. the first paint must draw every occupied cell, and the scores -----------
b = _Board(START, score=24, best=100)
b._refresh()
check("first refresh paints the occupied tiles",
      {(0, 0), (1, 1), (3, 0), (3, 3)} <= b.touched())
check("first refresh renders the tile values",
      [b.tiles[0][0].text, b.tiles[1][1].text, b.tiles[3][3].text]
      == ["2", "4", "16"])
check("first refresh sets the tile style classes",
      ("t-2" in b.tiles[0][0].ctx.classes
       and "t-16" in b.tiles[3][3].ctx.classes))
check("first refresh writes the score readouts",
      (b.score_lbl.text, b.best_lbl.text) == ("24", "100"))

# 2. THE REGRESSION: a refresh that changes nothing must touch nothing -------
b.zero()
b._refresh()
check("idle refresh touches no tile", b.ops() == 0)
check("idle refresh touches no score readout",
      b.score_lbl.ops == 0 and b.best_lbl.ops == 0)

# 3. a real move must repaint the changed cells and ONLY those ---------------
# left move: row 3 slides 8,_,_,16 -> 8,16,_,_ ; nothing else moves
b.zero()
b.board[3] = [8, 16, 0, 0]
b.score = 40
b._refresh()
check("a move repaints exactly the changed cells",
      b.touched() == {(3, 1), (3, 3)})
check("the vacated cell is cleared", b.tiles[3][3].text == "")
check("the vacated cell drops its tile class",
      not any(c.startswith("t-") for c in b.tiles[3][3].ctx.classes))
check("the arrived cell shows the merged value",
      b.tiles[3][1].text == "16" and "t-16" in b.tiles[3][1].ctx.classes)
check("a changed score is rewritten", b.score_lbl.text == "40")
check("an unchanged best score is left alone", b.best_lbl.ops == 0)

# 4. a repainted cell must not accumulate stale classes ---------------------
b.zero()
b.board[0][0] = 4
b._refresh()
check("a re-valued cell carries one tile class only",
      {c for c in b.tiles[0][0].ctx.classes if c.startswith("t-")} == {"t-4"})

# 5. values past the palette still get the fallback class -------------------
b.zero()
b.board[0][0] = 4096
b._refresh()
check("an off-palette value uses t-super",
      {c for c in b.tiles[0][0].ctx.classes if c.startswith("t-")}
      == {"t-super"})

# 6. the end-of-game banners must not force a full board repaint ------------
b.zero()
b.status = "lose"
b._refresh()
check("raising the no-moves banner repaints no tile", b.ops() == 0)
b.zero()
b.status = "play"
b._refresh()
check("dismissing the banner repaints no tile", b.ops() == 0)

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
