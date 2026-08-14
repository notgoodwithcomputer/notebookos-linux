#!/usr/bin/env python3
"""The balance chart must tell the truth, and must draw.

The chart is the only claim this app makes about money that is not a number you
can read back — it is a picture, and a picture is exactly the thing nobody
checks. Nothing tested it before this file.

Two separate jobs here:

  * `_balance_series` is the DATA. Every point must be the running total to that
    point, cent-exact, for a ledger of any shape. It is one of the three
    independent routes to the balance that `accounting_session_selftest` makes
    agree; this file checks it on its own, including at the sizes and values
    where floating point stops being obvious.
  * `_render_chart` is the DRAWING. It must survive the degenerate ledgers — no
    entries, one entry, every value identical, a zero opening — each of which
    reaches for a divisor that is zero (`vmax - vmin`, and `n - 1`). And it must
    actually put ink on the surface: a chart that silently draws nothing looks
    like a flat ledger rather than a broken renderer, which is the failure this
    file's last check exists for.

THERE ARE TWO RENDERERS, and testing one is testing the wrong half. `_render_chart`
draws the exported PDF; `_paint_chart` draws what is on screen all day. The first
version of this file covered only the PDF. Both are exercised now.

"IT PUT INK DOWN" IS TOO COARSE A QUESTION. The chart fills an area, strokes a
zero baseline and draws a border before it ever gets to the balance line — so a
check for "some pixel differs from the background" stays green with the LINE
deleted entirely. Measured: removing the stroke left that check passing. The
line is counted specifically, as pixels dark enough to be INK (#1A1916) rather
than the 6%-alpha fill or the pale grid.

RED PROOFS (M1), measured, each mutation applied alone:

  1. the series stops accumulating
     (`b = round(b + t["amt"], 2)` -> `b = round(t["amt"], 2)`)
       FAIL it ends at the ledger's balance
       FAIL every point is the running total to that point
       FAIL a thousand one-cent entries ends exactly where it should
       ...and the rest of the cent-exact family. 5 FAILED.
  2. the balance line is never stroked (`return` before `cr.set_line_width(1.6)`)
       FAIL the exported chart strokes a balance line too
            <- only 0 dark pixels on the surface
     Note what does NOT fail there: "the chart actually puts ink on the surface"
     stays green, because the fill and border are still drawn. That is the whole
     reason the dark-pixel count exists.
  3. the flat-ledger guard is removed (`if vmax == vmin: vmax = vmin + 1.0`,
     which appears in BOTH renderers)
       FAIL an empty ledger still draws
            <- ZeroDivisionError: float division by zero
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctchart-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/accounting.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import cairo                                                  # noqa: E402
import accounting                                             # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def build(amounts, opening=0.0):
    with open(STORE, "w") as f:
        json.dump({"opening": opening,
                   "tx": [{"date": "01 Jan", "iso": "2026-01-%02d" % (1 + i % 28),
                           "desc": "e%d" % i, "amt": a}
                          for i, a in enumerate(amounts)]}, f)
    a = accounting.Accounting()
    pump()
    return a


# ------------------------------------------------------------------- the data
app = build([-950.0, 2400.0, -51.4, -22.99, 625.0], opening=2400.0)
series = app._balance_series()
check("the series has one point per entry, plus the opening",
      len(series) == len(app.tx) + 1, (len(series), len(app.tx)))
check("it starts at the opening balance",
      series and series[0] == 2400.0, series[:1])
check("it ends at the ledger's balance",
      series and series[-1] == round(2400.0 + sum(t["amt"] for t in app.tx), 2),
      series[-1:])

# EVERY point, not just the ends — an accumulator that resets, or one that
# double-counts, still lands on the right total surprisingly often.
want = 2400.0
bad_point = None
for i, t in enumerate(app.tx):
    want = round(want + t["amt"], 2)
    if series[i + 1] != want:
        bad_point = "point %d: series %r, expected %r" % (i + 1, series[i + 1],
                                                          want)
        break
check("every point is the running total to that point", bad_point is None,
      bad_point or "")
app.destroy()
pump()

# ------------------------------------------------- cent-exact, not merely close
for name, amounts, opening in (
        ("a thousand one-cent entries", [0.01] * 1000, 0.0),
        ("seven sub-cent credits", [0.005] * 7, 0.0),
        ("a million cancelling itself", [1e6, -1e6], 0.0),
        ("large magnitudes", [1234567.89, -1234567.89], 0.0),
        ("a sub-cent opening", [1.0], 0.005)):
    a = build(amounts, opening)
    s = a._balance_series()
    exact = round(a.opening + sum(t["amt"] for t in a.tx), 2)
    check("%s ends exactly where it should" % name,
          s and s[-1] == exact, (s[-1] if s else None, exact))
    a.destroy()
    pump()

# ------------------------------------------------------------------ the drawing
def draw(a, w=400, h=200):
    """Render the chart to a real surface; return the surface, or the
    exception it raised."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    try:
        a._render_chart(cr, 0, 0, w, h, a._balance_series())
    except Exception as exc:                                  # noqa: BLE001
        return exc
    surf.flush()
    return surf


DEGENERATE = (
    ("an empty ledger", [], 0.0),
    ("a single entry", [-12.34], 0.0),
    ("every balance identical", [0.0, 0.0, 0.0], 100.0),
    ("a zero opening and one credit", [50.0], 0.0),
    ("all credits, never below zero", [10.0, 20.0, 30.0], 500.0),
)
for name, amounts, opening in DEGENERATE:
    a = build(amounts, opening)
    got = draw(a)
    check("%s still draws" % name, not isinstance(got, Exception),
          "%s: %s" % (type(got).__name__, got) if isinstance(got, Exception)
          else "")
    a.destroy()
    pump()

# ------------------------------------------------------- and it puts ink down
# A chart that raises nothing and draws nothing reads as a flat ledger, not as a
# broken renderer. Compare against the surface's own background colour rather
# than against "not transparent": _render_chart fills its rect with BG first, so
# an all-BG surface means the line never landed.
a = build([-950.0, 2400.0, -51.4, 625.0], opening=2400.0)
surf = draw(a)
check("a normal ledger renders without raising", not isinstance(surf, Exception),
      surf if isinstance(surf, Exception) else "")
if not isinstance(surf, Exception):
    data = bytes(surf.get_data())
    # The most common byte-quad is the background it filled; if anything else
    # appears in quantity, something was drawn on top of it.
    quads = {}
    for i in range(0, len(data) - 3, 4):
        q = data[i:i + 4]
        quads[q] = quads.get(q, 0) + 1
    ordered = sorted(quads.values(), reverse=True)
    distinct = len(quads)
    check("the chart actually puts ink on the surface", distinct >= 3,
          "only %d distinct pixel values on the surface" % distinct)
    check("...and the ink is more than a stray pixel",
          len(ordered) > 1 and ordered[1] > 50,
          "second-most-common pixel appears %d times"
          % (ordered[1] if len(ordered) > 1 else 0))
a.destroy()
pump()

# ------------------------------------------- the ON-SCREEN chart, not just the PDF
# There are TWO renderers: _render_chart draws the exported PDF, _paint_chart
# draws what is on screen all day. Testing only the first would have left the
# one the user actually looks at uncovered.
def paint(a, w=560, h=180):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    try:
        a._paint_chart(cr, w, h)
    except Exception as exc:                                  # noqa: BLE001
        return exc
    surf.flush()
    return surf


def dark_pixels(surf):
    """Pixels dark enough to be the balance LINE (INK #1A1916) rather than the
    6%-alpha area fill or the pale grid. Counting "any ink at all" is too coarse
    to notice the line going missing — measured: deleting the stroke left the
    fill and border behind and a plain ink check stayed green."""
    data = bytes(surf.get_data())
    n = 0
    for i in range(0, len(data) - 3, 4):
        b, g, r = data[i], data[i + 1], data[i + 2]
        if r < 110 and g < 110 and b < 110:
            n += 1
    return n


for name, amounts, opening in (("a normal ledger", [-950.0, 2400.0, -51.4, 625.0], 2400.0),
                               ("an empty ledger", [], 0.0),
                               ("a single entry", [-12.34], 0.0),
                               ("every balance identical", [0.0, 0.0], 100.0)):
    a = build(amounts, opening)
    got = paint(a)
    check("the on-screen chart survives %s" % name,
          not isinstance(got, Exception),
          "%s: %s" % (type(got).__name__, got) if isinstance(got, Exception)
          else "")
    if name == "a normal ledger" and not isinstance(got, Exception):
        n = dark_pixels(got)
        check("...and actually strokes a balance line", n > 100,
              "only %d dark pixels on the surface" % n)
    a.destroy()
    pump()

# The same, for the PDF renderer: the line, not merely ink.
a = build([-950.0, 2400.0, -51.4, 625.0], opening=2400.0)
surf = draw(a)
if not isinstance(surf, Exception):
    n = dark_pixels(surf)
    check("the exported chart strokes a balance line too", n > 100,
          "only %d dark pixels on the surface" % n)
a.destroy()
pump()

# ------------------------------------------------ the cache follows the screen
# The chart is rendered once into an ImageSurface and blitted, because every
# expose repaints in software on this hardware. That cache is keyed on the
# allocation AND on the device scale — the source comment says so, and a comment
# saying so is exactly the kind of claim that has been wrong twice today, so it
# is measured here. Without the scale in the key, a window dragged to a HiDPI
# monitor keeps blitting the surface built for the old one and the chart is soft
# while the table beside it is sharp.
a = build([-950.0, 2400.0], opening=2400.0)
off = Gtk.OffscreenWindow()
kid = a.get_child()
a.remove(kid)
off.add(kid)
off.set_size_request(1024, 722)
off.show_all()
pump()
off.get_pixbuf()
pump()


def redraw():
    alloc = a.chart.get_allocation()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(alloc.width, 40),
                              max(alloc.height, 40))
    a._draw_chart(a.chart, cairo.Context(surf))
    return a._chart_cache


c1 = redraw()
check("the chart gets a real allocation and caches a surface for it",
      c1 and c1[0] > 20 and c1[2].get_width() == c1[0],
      c1 and (c1[0], c1[1], c1[2].get_width()))

a.chart.get_scale_factor = lambda: 2       # pretend it moved to a HiDPI panel
c2 = redraw()
check("a device-scale change re-renders the cache at 2x pixels",
      c2 and c2[3] == 2 and c2[2].get_width() == c2[0] * 2,
      c2 and (c2[3], c2[2].get_width(), c2[0]))

a.chart.get_scale_factor = lambda: 1
c3 = redraw()
check("...and going back to 1x re-renders again",
      c3 and c3[3] == 1 and c3[2].get_width() == c3[0],
      c3 and (c3[3], c3[2].get_width()))
off.destroy()
a.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
