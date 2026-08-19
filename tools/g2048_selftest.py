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

    def get_accessible(self):
        return self

    def set_name(self, name):
        self.accessible_name = name


class _Widget(object):
    def get_accessible(self):
        return self

    def set_name(self, name):
        self.accessible_name = name

    def grab_focus(self):
        self.focused = True

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
        self.again_btn = _Widget()
        self._overlay_status = None

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
check("every board cell exposes coordinates and value or empty state",
      b.tiles[0][0].accessible_name == "Row 1, column 1: 2"
      and b.tiles[0][1].accessible_name == "Row 1, column 2: Empty"
      and b.tiles[3][3].accessible_name == "Row 4, column 4: 16")

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
check("accessible cell state follows a move",
      b.tiles[3][1].accessible_name == "Row 4, column 2: 16"
      and b.tiles[3][3].accessible_name == "Row 4, column 4: Empty")
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

# 7. the motion layer (PAPER-PHYSICS G4 reference: content.2048) ------------
# Widget-free like everything above: the traced slide is pure, the draw
# handler runs on a stand-in against a real cairo surface, and instant
# equivalence is proven through the real engine with a clockless widget.

# 7a. provenance must agree with the game's own arithmetic — exhaustively.
import itertools                                              # noqa: E402
disagree = 0
for line in itertools.product((0, 2, 4, 8), repeat=4):
    res_a, gain_a = g2048.Game2048._slide(list(line))
    res_b, gain_b, tj, tm = g2048.Game2048._slide_traced(list(line))
    if res_a != res_b or gain_a != gain_b:
        disagree += 1
check("traced slide agrees with _slide on all 256 lines", disagree == 0)
res, gain, tj, tm = g2048.Game2048._slide_traced([2, 2, 4, 0])
check("traced journeys carry pre-merge values",
      res == [4, 4, 0, 0] and gain == 4
      and tj == [(0, 0, 2), (1, 0, 2), (2, 1, 4)] and tm == [0])

# 7b. the slide phase INTERPOLATES: rendered ink must move with v.
import cairo                                                  # noqa: E402


class _Layer(object):
    def __init__(self):
        self.shown = self.hidden = self.draws = 0

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def queue_draw(self):
        self.draws += 1


class _Stand(object):
    # _rounded is a staticmethod on the app class; rebinding the bare
    # function here would re-inject self and shift every argument.
    _rounded = staticmethod(g2048.Game2048._rounded)
    _paint_tile = g2048.Game2048._paint_tile
    _anim_draw = g2048.Game2048._anim_draw
    _anim_frame = g2048.Game2048._anim_frame
    _begin_settle = g2048.Game2048._begin_settle
    _anim_end = g2048.Game2048._anim_end
    _finish_anim_now = g2048.Game2048._finish_anim_now

    def __init__(self):
        self.anim_layer = _Layer()
        self._wells = None
        self._geom = {(r, c): (10 + c * 70, 10 + r * 70, 60, 60)
                      for r in range(4) for c in range(4)}
        self.board = [[0] * 4 for _ in range(4)]
        self._anim_phase = "slide"
        self._anim_v = 0.0
        self._anim_data = {"journeys": [(2, (0, 0), (0, 3))],
                           "statics": [(4, (2, 2))],
                           "merges": set(), "spawn": None}

    def _ensure_geom(self):
        return self._geom


def _render(stand, v, phase="slide"):
    stand._anim_phase = phase
    stand._anim_v = v
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 320)
    stand._anim_draw(None, cairo.Context(surf))
    surf.flush()
    return bytes(surf.get_data())


st = _Stand()
f0, fh, f1 = _render(st, 0.0), _render(st, 0.5), _render(st, 1.0)
check("slide frames differ as v advances (the tile actually travels)",
      f0 != fh and fh != f1 and f0 != f1)


def _px(buf, x, y):
    off = (y * 320 + x) * 4
    return buf[off:off + 4]


check("a static tile's ink does not move between frames",
      _px(f0, 180, 180) == _px(f1, 180, 180)
      and _px(f0, 180, 180) != b"\x00\x00\x00\x00")
check("the mover has left its origin by the end",
      _px(f0, 40, 40) != _px(f1, 40, 40))

st.board[1][1] = 2
st._anim_data = {"journeys": [], "statics": [], "merges": set(),
                 "spawn": (1, 1, 2)}
s0, s1 = _render(st, 0.0, "settle"), _render(st, 1.0, "settle")
check("a spawn is absent at v=0 and present at v=1",
      _px(s0, 110, 110) == b"\x00\x00\x00\x00"
      and _px(s1, 110, 110) != b"\x00\x00\x00\x00")

# 7c. instant equivalence through the REAL engine: a clockless layer means
# both phases complete synchronously and the overlay never survives the call.
st2 = _Stand()
st2._anim = None
st2._wells = object()      # the guard reads "no wells = no engine"; arm it
g2048.Game2048._animate_move(st2, [(2, (0, 0), (0, 1))], [], [], None)
check("instant path: overlay shown then hidden within the call",
      st2.anim_layer.shown == 1 and st2.anim_layer.hidden >= 1)
check("instant path: no animation phase survives",
      st2._anim_phase is None and st2._anim_data is None)

# 7d. a key mid-flight lands the previous journey before the next begins.
st3 = _Stand()
st3._anim = None
g2048.Game2048._finish_anim_now(st3)
check("finish-now clears phase, data and the overlay",
      st3._anim_phase is None and st3._anim_data is None
      and st3.anim_layer.hidden == 1)

# 8. A restored terminal board must not remain stuck in "play". The normal
# move path detects game-over after a successful slide, but a crash can leave
# the immediately-saved high-score board on disk before that final check. On
# reopen every direction is a no-op, so the no-op branch itself must raise the
# banner and persist that the finished board should not be resumed again.
terminal = g2048.Game2048.__new__(g2048.Game2048)
terminal.board = [
    [2, 4, 2, 4],
    [4, 2, 4, 2],
    [2, 4, 2, 4],
    [4, 2, 4, 2],
]
terminal.status = "play"
terminal._finish_anim_now = lambda: None
terminal._refreshes = 0
terminal._saves = 0
terminal._refresh = lambda: setattr(
    terminal, "_refreshes", terminal._refreshes + 1)
terminal._save_best = lambda: setattr(
    terminal, "_saves", terminal._saves + 1)
terminal.move("left")
check("a no-op key recognizes a restored terminal board",
      terminal.status == "lose" and terminal._refreshes == 1)
check("recognizing the terminal board persists it as finished",
      terminal._saves == 1)

# A first 2048 and a full terminal board can happen on the same move. Winning
# must be shown before Game Over; Continue may reveal the terminal state later.
winner = g2048.Game2048.__new__(g2048.Game2048)
winner.board = [
    [1024, 1024, 4, 8],
    [4, 8, 16, 4],
    [8, 16, 4, 8],
    [16, 4, 8, 16],
]
winner.status = "play"; winner._won_shown = False
winner.score = winner.best = 0
winner._finish_anim_now = lambda: None
def _spawn_last():
    winner.board[0][3] = 2
    return (0, 3, 2)
winner._add_random = _spawn_last
winner._save_best = lambda: None
winner._queue_save = lambda: None
winner._refresh = lambda: None
winner._animate_move = lambda *_a: None
winner.move("left")
check("a terminal move that creates 2048 shows the win before loss",
      winner.board[0] == [2048, 4, 8, 2]
      and not winner._can_move() and winner.status == "win")

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
