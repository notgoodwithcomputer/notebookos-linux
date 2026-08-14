#!/usr/bin/env python3
"""The keypad is a number pad, and the whole card fits the smallest panel.

    tools/guestrun.sh python3 tools/calculator_layout_selftest.py

THREE defects, all invisible to every gate this app had, all found by rendering
the app at 1024x722 and looking at the picture. The third is at the bottom: a
column of numbers in the Table view was CENTRE-aligned, so the decimal points
wandered row to row and the column could not be read downwards.

DEFECT 1 -- the digits were not in a number pad. KEYS is written in six groups
and five of them are six wide and read exactly as a calculator does:

    sqrt pi  7 8 9 div        x2  e   4 5 6 x
    1/x  x!  1 2 3 -          +/- %   0 . = +

The FIRST group is eight keys, the function row. Folding all 38 with
`divmod(i, 6)` therefore pushed everything after the first row two cells left,
and what reached the screen was

    )   bksp sqrt pi  7 8         <- 7 and 8 marooned at the end of a row
    9   div  x2   e   4 5         <- 9 alone at the start of the next
    6   x    1/x  x!  1 2
    3   -    +/-  %   0 .
    =   +    .    .   .  .        <- and a hole four cells wide

Nothing was wrong with the LIST -- it was folded at the wrong width. The eight
function keys now get their own eight-wide strip and the remaining thirty fold
at six into five full rows, so no key had to be added, moved or removed and
there is no empty cell anywhere. That is why this suite checks ADJACENCY rather
than a table of coordinates: the promise is "7 8 9 sit together above 4 5 6",
not "7 is at column 2", and a check written as coordinates would have to be
rewritten by anyone who legitimately moves the block.

DEFECT 2 -- the card did not fit the OS's own smallest panel. `_keypad`'s
sibling line read `sw, _sh = nbapp.screen_size()`: the card sized itself from
the screen WIDTH and threw the height away. At 1024x768 the card wants 732px
and has 595 once the 46px shell strut, the view bar and the stage padding are
taken out, so the bottom of the keypad -- "=" among it -- sat below the fold and
had to be scrolled to.

WHY minsize_sweep PASSED IT AT 1024x722, which is the part worth keeping: that
gate measures the WINDOW, and this app's home page lives in a ScrolledWindow.
A ScrolledWindow reports a small fixed minimum height whatever it contains, so
the window's preferred height came back 556 both before and after -- the number
does not move because it CANNOT move. Every scrolled app in the OS passes that
gate for free. Measured here instead against what the card actually gets:
    before  card 732  avail 595   57px of keypad below the fold
    after   card 507  avail 647   140px of headroom, notice up: 97px

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALCULATOR_MODULE_DIR. MEASURED -- my first four predictions were wrong
in three places and the corrections are more interesting than the guesses:

  1. THE SHIPPED BUG restored: all 38 keys folded at six again
     (the strip/pad branch -> `grid = pad; r, c = divmod(i, PAD_COLS)`)
                                                                  4 FAILED
       FAIL 7 8 9 are adjacent, left to right   <- columns 4 5, then a row break
       FAIL 4 5 6 are adjacent, left to right
       FAIL 1 2 3 are adjacent, left to right
       FAIL the pad has no empty cells          <- 4 empty at the last row
     "the number block is square" stays GREEN here, and that is worth knowing
     rather than hiding: under the old fold 7/4/1/0 really did land in one
     column one row apart -- it was the HORIZONTAL grouping that broke. A suite
     with only the column check would have called the scrambled pad correct.

  2. compact mode never engages
     (`self._compact = sh < 860` -> `self._compact = False`)       2 FAILED
       FAIL the whole card fits a 1024x722 panel  <- card 732 > avail 595
       FAIL ...and compact mode is what makes it fit at this size

  3. every compact metric tuned back to its full-size value
     (stage padding 14->40, key 52->66, display padding restored) 1 FAILED
       FAIL the whole card fits a 1024x722 panel
     Tuning back only `.compact .key` does NOT fail, measured: there is 140px of
     headroom now, so one metric can move without breaking the fit. The check
     guards the OUTCOME, not any single number, which is the right granularity
     -- but it does mean a partial regression can hide until it accumulates.

  5. table cells go back to the default alignment
     (`Gtk.Label(label=text, xalign=1.0)` -> `Gtk.Label(label=text)`)
                                                                  1 FAILED
       FAIL every table value is right-aligned, so the column lines up

  6. the header stops being styled as a header
     (both cells get "tblcell")                                   1 FAILED
       FAIL the header row is styled as a header, not as data

  4. a digit is moved out of the block
     (the "5" key's entry replaced by a second "x!")               1 FAILED
       FAIL 4 5 6 are adjacent, left to right
     Only one, because removing "5" also removes it from `pos`, and the column
     check does not name it. Predicted two.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-layout-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import dialogshot                                             # noqa: E402
import nbapp                                                  # noqa: E402

W, H = 1024, 722
uishot.load_theme()
nbapp.screen_size = lambda: (W, H)
import calculator                                             # noqa: E402
dialogshot.install_app_css(calculator)

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def walk(w, out=None):
    out = [] if out is None else out
    out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            walk(c, out)
    return out


def by_class(root, cls):
    return [w for w in walk(root)
            if hasattr(w, "get_style_context")
            and w.get_style_context().has_class(cls)]


app = calculator.Calculator()
child = app.get_child()
app.remove(child)
off = Gtk.OffscreenWindow()
off.set_size_request(W, H)
off.add(child)
off.show_all()
child.set_size_request(W, H)
for _ in range(80):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)

# ------------------------------------------------------------- where each key is
# Read the positions off the REAL grids rather than recomputing them from KEYS:
# recomputing would re-derive the layout from the same arithmetic that produced
# the bug, and agree with it.
grids = [w for w in walk(child) if isinstance(w, Gtk.Grid)
         and w.get_style_context().has_class("keypad")]
pos = {}          # label -> (grid index, col, row)
for gi_, g in enumerate(grids):
    for kid in g.get_children():
        c = g.child_get_property(kid, "left-attach")
        r = g.child_get_property(kid, "top-attach")
        labels = [x for x in walk(kid) if isinstance(x, Gtk.Label)]
        if labels and labels[0].get_text():
            pos[labels[0].get_text()] = (gi_, c, r)

check("the keypad is built as a strip plus a pad", len(grids) == 2,
      "%d grid(s) with class keypad" % len(grids))


def rowof(d):
    return pos.get(d, (None, None, None))


def adjacent(a, b, c):
    """a b c on ONE row of ONE grid, in three consecutive columns."""
    pa, pb, pc = rowof(a), rowof(b), rowof(c)
    if None in pa or None in pb or None in pc:
        return False, "missing: %s" % [k for k in (a, b, c) if k not in pos]
    if not (pa[0] == pb[0] == pc[0]):
        return False, "different grids: %s %s %s" % (pa, pb, pc)
    if not (pa[2] == pb[2] == pc[2]):
        return False, "different rows: %s=%s %s=%s %s=%s" % (
            a, pa[2], b, pb[2], c, pc[2])
    return (pb[1] == pa[1] + 1 and pc[1] == pb[1] + 1), \
        "columns %s %s %s" % (pa[1], pb[1], pc[1])


for trio in (("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3")):
    ok, why = adjacent(*trio)
    check("%s %s %s are adjacent, left to right" % trio, ok, why)

# ...and the three rows stack, so the block is a block and not a staircase.
sq = True
why = ""
try:
    r7, r4, r1 = rowof("7"), rowof("4"), rowof("1")
    r0 = rowof("0")
    sq = (r7[1] == r4[1] == r1[1] == r0[1]
          and r4[2] == r7[2] + 1 and r1[2] == r4[2] + 1 and r0[2] == r1[2] + 1)
    why = "7=%s 4=%s 1=%s 0=%s" % (r7, r4, r1, r0)
except Exception as exc:                                 # pragma: no cover
    sq, why = False, str(exc)
check("the number block is square (7/4/1/0 in one column, one row apart)",
      sq, why)

# ------------------------------------------------------------ no holes in the pad
pad = grids[-1]
cells = set()
maxc = maxr = 0
for kid in pad.get_children():
    c = pad.child_get_property(kid, "left-attach")
    r = pad.child_get_property(kid, "top-attach")
    cells.add((c, r))
    maxc, maxr = max(maxc, c), max(maxr, r)
missing = [(c, r) for r in range(maxr + 1) for c in range(maxc + 1)
           if (c, r) not in cells]
check("the pad has no empty cells", not missing,
      "%d empty: %s" % (len(missing), missing[:6]))

# ------------------------------------------------- and the whole thing fits 1024x722
card = by_class(child, "calccard")[0]
stage = by_class(child, "calcstage")[0]
nav = by_class(child, "calcnav")[0]
CARD_W = min(640, max(320, W - 96))
card_h = card.get_preferred_height_for_width(CARD_W)[0]
pad_h = stage.get_preferred_height_for_width(CARD_W)[0] - card_h
avail = H - nav.get_preferred_height()[0] - max(0, pad_h)
check("the whole card fits a 1024x722 panel", card_h <= avail,
      "card %s > avail %s (%s px of keypad below the fold). minsize_sweep "
      "cannot see this: it measures the window, and the home page is inside a "
      "ScrolledWindow that reports a small fixed minimum whatever it holds."
      % (card_h, avail, card_h - avail))
check("...and compact mode is what makes it fit at this size",
      getattr(app, "_compact", None) is True, getattr(app, "_compact", None))

# The roomier layout must survive on a screen that can afford it -- a fix that
# made every machine compact would pass the check above and be a regression.
nbapp.screen_size = lambda: (1920, 1080)
big = calculator.Calculator()
check("a 1920x1080 screen still gets the full-size layout",
      getattr(big, "_compact", None) is False, getattr(big, "_compact", None))

# ------------------------------------------------- the TABLE reads as a column
# A column of numbers is read downwards. Every cell used to be a plain
# Gtk.Label, which CENTRES, so `0.0174524064373` sat over `0.13917310096` over
# `0.5` with nothing lined up and the decimal points wandering row to row.
nbapp.screen_size = lambda: (W, H)
tab = calculator.Calculator()
tab.ys = ["sin(X)", "", "", ""]
tab.y_enabled = [True, False, False, False]
tab._refresh_table()
cells = tab.table_grid.get_children()
check("the table has cells to check", len(cells) > 10, len(cells))

rows = {}
for kid in cells:
    c = tab.table_grid.child_get_property(kid, "left-attach")
    r = tab.table_grid.child_get_property(kid, "top-attach")
    rows.setdefault(r, {})[c] = kid

body = [k for r, cs in rows.items() if r > 0 for k in cs.values()]
centred = [k.get_text() for k in body if abs(k.get_xalign() - 1.0) > 0.01]
check("every table value is right-aligned, so the column lines up",
      not centred, "%d centred: %s" % (len(centred), centred[:4]))

heads = list(rows.get(0, {}).values())
check("the header row is styled as a header, not as data",
      heads and all(k.get_style_context().has_class("tblhead") for k in heads),
      [k.get_text() for k in heads])
check("...and the values are not",
      all(k.get_style_context().has_class("tblcell") for k in body),
      "some body cell lacks tblcell")

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
