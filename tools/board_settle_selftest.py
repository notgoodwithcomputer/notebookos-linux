#!/usr/bin/env python3
"""board_settle_selftest — the desktop board's staggered arrival (G1).

Cards settle in along their columns: one linear global value, each card's
own progress a clamped remap offset by its column, eased on arrival. This
drives the remap and the draw translate as plain methods over a stand-in —
no display — and proves the stagger's ordering, the exact landing, and the
translate's rest state. Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="board-settle-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import widgets                                                # noqa: E402
import nbmotion                                               # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


class _Cr(object):
    def __init__(self):
        self.moves = []

    def translate(self, dx, dy):
        self.moves.append((dx, dy))


class _Card(object):
    def __init__(self, col):
        self._nb_col = col


class _Stand(object):
    _SETTLE_RISE = widgets.Widgets._SETTLE_RISE
    _SETTLE_STAG = widgets.Widgets._SETTLE_STAG
    _settle_t = widgets.Widgets._settle_t
    _card_settle_draw = widgets.Widgets._card_settle_draw

    def __init__(self, v):
        self._settle_v = v


# 1. stagger ordering: at an early global value, column 0 has moved and the
# right column has not started.
st = _Stand(0.18)
t0, t3 = st._settle_t(0), st._settle_t(widgets.TILE_COLS)
check("column 0 leads the right column", t0 > 0.0 and t3 == 0.0)

# 2. monotone per column, and every column lands exactly on 1
ok = True
for col in range(widgets.TILE_COLS + 1):
    prev = -1.0
    for i in range(0, 101):
        st._settle_v = i / 100.0
        t = st._settle_t(col)
        if t < prev - 1e-9:
            ok = False
        prev = t
    st._settle_v = 1.0
    if st._settle_t(col) != 1.0:
        ok = False
check("every column is monotone and lands exactly on 1", ok)

# 3. the settle is a damped arrival: the eased early step outruns linear
st._settle_v = 0.10
lead = st._settle_t(0)
check("arrival is eased, not linear",
      lead > 0.10 and lead == nbmotion.ease_out(
          (0.10 * (nbmotion.SURFACE_IN / 1000.0
                   + st._SETTLE_STAG * widgets.TILE_COLS))
          / (nbmotion.SURFACE_IN / 1000.0)))

# 4. the draw translate: moving card offset upward, landed card untouched
st._settle_v = 0.18
cr = _Cr()
st._card_settle_draw(_Card(widgets.TILE_COLS), cr)
check("an unstarted card is held at the full rise",
      cr.moves == [(0, -float(st._SETTLE_RISE))])
st._settle_v = 1.0
cr = _Cr()
st._card_settle_draw(_Card(0), cr)
check("a landed card's paint is not translated at all", cr.moves == [])

# 5. no engine, no motion: the remap answers 1.0 whatever the value
real = widgets.nbmotion
widgets.nbmotion = None
try:
    st._settle_v = 0.3
    check("without the engine every card is at rest", st._settle_t(1) == 1.0)
finally:
    widgets.nbmotion = real

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
