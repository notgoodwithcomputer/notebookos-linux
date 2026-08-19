#!/usr/bin/env python3
"""Plotting a curve gives the same numbers as typing them, and does it quickly.

    tools/guestrun.sh python3 tools/calculator_graph_selftest.py

The graph view evaluates one expression at 401 points, for each enabled
function, on every redraw -- and a redraw is what a zoom, a pan and every press
of an arrow key while tracing all cause. `_eval_x` used to substitute the sample
INTO the source, so `sin(X)` became `sin((1.2345))` and each of those 401 points
was a brand-new string to be re-mangled, re-parsed, re-guarded and re-compiled.

    one _draw_graph, one curve, 900x500, five INTERLEAVED rounds:
        before   median 129.0 ms  (min 86.7, max 238.1)   ~8fps
        after    median  39.8 ms  (min 16.6, max  44.7)   ~25fps
                 3.2x on medians, 5.2x on bests

    Interleaved because it had to be. The first before/after pair I took said
    8x; the second, minutes later on the same box, said 1.6x. Neither was a
    measurement. Alternating the two builds five times and comparing medians is.

X is now bound in the evaluation environment rather than pasted into the text.
Single uppercase letters are already this calculator's variable names, so
nothing new was needed to resolve it -- and because the source string no longer
changes per sample, one compiled form serves the whole curve.

THE RISK THAT CAME WITH IT, which is what most of this file is about: a cache
keyed on source text is only correct if the source is the ONLY thing that
decides the answer. It is not. The same `sin(X)` means different numbers in
degrees and in radians, `A+1` means different numbers as A changes, and `Ans`
changes with every "=". None of those live in the source. They live in the
environment, which is rebuilt per call -- so the cache holds the parse, never a
value. These checks exist to keep it that way, because the tempting next
optimisation is to cache the RESULT, and that would be wrong in four measurable
ways.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALCULATOR_MODULE_DIR. All four MEASURED:

  1. X stops being bound in the environment
     (`env["X"] = self._x_bind` -> `pass`)                       13 FAILED
       every plotted value and every equivalence check: X becomes an unknown
       name and each sample fails as "Error".

  2. the compile cache is keyed on a constant, so every expression is handed
     the FIRST one's code
     (`_CODE_CACHE.get(js)` -> `.get("k")`, `[js] =` -> `["k"] =`)
                                                                 14 FAILED
       FAIL X^2 at X=3 is 9        <- 42.0, still `X+1`'s code
       ...and everything after it, including the equivalence pairs.
     This is the proof that matters. A careless key does not raise -- it draws a
     plausible, wrong curve.

  3. the angle mode stops reaching the environment
     (`"sin": dsin if self.deg else math.sin` -> `"sin": dsin`)   1 FAILED
       FAIL ...and not 1 in radians
     One, not two: sin(90) in DEGREES is still right under this mutation, which
     is exactly why the pair is written as a pair. A single check on the
     degrees value would have called always-degrees correct.

  5. Zoom Fit stops padding a flat curve
     (the `if not hi > lo:` pad removed)                           8 FAILED
       FAIL Zoom Fit on the constant 5 / 0 / X-X / -3.5 leaves a window
            with height, and each of the four "...and the graph still draws"
            that follow it (ZeroDivisionError in the draw handler).

  6. window_is_valid stops screening for finiteness
     (`if not all(math.isfinite(...))` removed)                    1 FAILED
       FAIL an infinite bound is refused
     ONE, not four, and the reason is the finding: every NaN case is still
     refused without that line, because the rule is written POSITIVELY --
     `xmin < xmax and ymin < ymax and ...`. NaN fails a positive test. The
     shipped guard was the negative of it, `if xmin >= xmax or ...: raise`,
     and NaN fails that too, which means it was NOT rejected. The hole was in
     the SHAPE of the guard, not in a missing condition; expressing the rule as
     what a good window IS closes it. The explicit finiteness line is what
     catches inf, which orders perfectly well and is still not a window.

  7. the typed Table Step is unguarded again
     (the isfinite / non-zero screen removed from _table_setting) 5 FAILED
       FAIL a Table Step of '0' / 'nan' / 'inf' / '-inf' / '1e400' leaves the
            old one alone
     Measured on the module as it stood, table showing Y1 = X: a step of 0 gave
     all 40 rows x = 0, nan gave 40 nan rows, inf gave inf with a nan first row.
     Zero is the interesting one — a plausible typo, taken in silence, producing
     a table that looks broken with nothing to say why.

  8. a stored zero step is loaded again
     (`_finite(...) or 1.0` -> `_finite(...)`)                    1 FAILED
       FAIL a stored Table Step of zero is not loaded
     _finite screens nan and inf. Zero parses perfectly well and is still not a
     step, which is the same gap the window had.

  4. THE TEMPTING ONE -- the result is cached, not just the parse
     (a `_RESULT_CACHE[js]` wrapped around the eval)               4 FAILED
       FAIL the sample shadows a stored variable called X
       FAIL a stored variable is re-read, not remembered from last time
       FAIL Ans updates between evaluations
       FAIL ...and not 1 in radians
     Those four are the whole argument for keying a cache on the parse and never
     on the answer, and they fail together the moment someone tries it.
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-graph-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import cairo                                                  # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402

uishot.load_theme()
nbapp.screen_size = lambda: (1024, 722)
import calculator                                             # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(app):
    for _ in range(10):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


app = calculator.Calculator()
pump(app)

# ------------------------------------------------------- the plotted numbers
CASES = [
    ("X+1", 41.0, 42.0),
    ("X^2", 3.0, 9.0),
    ("1/X", 4.0, 0.25),
    ("2X", 21.0, 42.0),          # implicit multiplication still applies
    ("sqrt(X)", 9.0, 3.0),
    ("X-X", 7.0, 0.0),
]
for expression, x, want in CASES:
    try:
        got = app._eval_x(expression, x)
    except Exception as exc:
        got = "raised %s: %s" % (type(exc).__name__, exc)
    check("%s at X=%g is %g" % (expression, x, want), got == want, got)

# ------------------------------- ...are the numbers you get by TYPING the same
# The equivalence that justifies the change. Plotting f at x and typing f with
# the number written in must agree -- that was true when _eval_x pasted the
# sample into the source, and binding it as a name must not have altered it.
def _same(a, b):
    """Compare two answers without CRASHING on a non-number.

    This read `str(a) == str(float(b))`, and under a sabotage that makes the
    typed form fail, float("Error") raised ValueError and took the whole suite
    down mid-run. A crashed suite is not a red proof -- it reports no result at
    all, and the launder is that a bare exit code looks like one failure. Both
    sides are normalised as text so a mismatch FAILS instead."""
    def norm(v):
        try:
            return "%.12g" % float(v)
        except (TypeError, ValueError):
            return str(v)
    return norm(a) == norm(b)


for expression, x, _want in CASES:
    typed = expression.replace("X", "(%r)" % x)
    app.expr = typed
    by_typing = app.evaluate()
    app.expr = ""
    try:
        by_plotting = app._eval_x(expression, x)
    except Exception as exc:
        by_plotting = "raised %s" % type(exc).__name__
    check("plotting %s matches typing %s" % (expression, typed),
          _same(by_plotting, by_typing),
          "plotted %r, typed %r" % (by_plotting, by_typing))

# --------------------------------------- a sample SHADOWS a stored variable X
# Documented behaviour, and it is what the old textual substitution did too:
# replace("X", ...) hit the letter wherever it appeared.
app.variables["X"] = 999.0
check("the sample shadows a stored variable called X",
      app._eval_x("X+1", 5.0) == 6.0, app._eval_x("X+1", 5.0))
del app.variables["X"]

# ------------------------------------ what the cache must NOT be allowed to hold
# Each of these is a value that lives in the environment, not in the source. A
# cache that held results instead of parses would return the first answer to all
# of them.
app.variables["A"] = 1.0
app.expr = "A+1"
first = app.evaluate()
app.variables["A"] = 10.0
app.expr = "A+1"
second = app.evaluate()
check("a stored variable is re-read, not remembered from last time",
      (first, second) == ("2", "11"), (first, second))

app.expr = "6*7"
app.evaluate()
app.ans = 42.0
app.expr = "Ans+1"
a1 = app.evaluate()
app.ans = 100.0
app.expr = "Ans+1"
a2 = app.evaluate()
check("Ans updates between evaluations", (a1, a2) == ("43", "101"), (a1, a2))

app.deg = True
app.expr = "sin(90)"
in_deg = app.evaluate()
app.deg = False
app.expr = "sin(90)"
in_rad = app.evaluate()
check("sin(90) is 1 in degrees", in_deg == "1", in_deg)
check("...and not 1 in radians", in_rad != "1", in_rad)
app.deg = True

# ------------------------------------------------------------------ and quickly
# A LOOSE bound on purpose. The point is to catch a return to re-compiling every
# sample (130ms measured, and it would be worse on the machines this ships to),
# not to police a few milliseconds -- an assertion tight enough to be a
# benchmark would fail on a loaded build box and get deleted by whoever hits it.
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 500)
cr = cairo.Context(surf)


class _Area(object):
    def get_allocated_width(self):
        return 900

    def get_allocated_height(self):
        return 500


app.ys = ["sin(X)", "", "", ""]
app.y_enabled = [True, False, False, False]
# COUNT the parses, do not time them, and count them at the PARSER.
#
# This check has been wrong three times and each way is worth recording:
#
#   "a redraw is under 60ms"   true alone, RED when ten suites ran back to back
#                              -- the exact fragility its own comment predicted.
#   warm-vs-cold RATIO         load does NOT cancel: scheduler noise is additive,
#                              so both halves inflate equally and the ratio is
#                              squeezed toward 1. Went red as collateral on two
#                              mutations that never touched the cache.
#   counting cache MISSES      invisible to a mutation that stops consulting the
#                              cache at all (`code = _CODE_CACHE.get(js)` ->
#                              `code = None` passed clean), because the counter
#                              only saw lookups that happened.
#
# The thing being defended is not milliseconds and not cache mechanics: it is
# that one curve parses its expression ONCE rather than 401 times. Counted at
# ast.parse, which every route to a compiled expression must go through however
# the caching above it is written. Exact, and no amount of load can move it.
class _CountingAst(object):
    def __init__(self, real):
        self._real = real
        self.parses = 0

    def parse(self, *a, **kw):
        self.parses += 1
        return self._real.parse(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


_ast = _CountingAst(calculator.ast)
_real_ast = calculator.ast
calculator.ast = _ast
try:
    app.ys = ["sin(X)", "", "", ""]
    app.y_enabled = [True, False, False, False]
    calculator._CODE_CACHE.clear()
    app._draw_graph(_Area(), cr)          # first draw of this curve
    first = _ast.parses
    _ast.parses = 0
    app._draw_graph(_Area(), cr)          # and again
    second = _ast.parses
finally:
    calculator.ast = _real_ast

check("a 401-sample curve parses its expression once, not once per sample",
      first <= 2, "%d parses for one curve" % first)
check("...and redrawing the same curve parses nothing",
      second == 0, "%d parses on the second draw" % second)

# --------------------------------------- a flat line still has a window to sit in
# `Zoom Fit` set ymin,ymax = min(vals),max(vals). For a CONSTANT function those
# are the same number, the window has no height, and graph_to_pixel divides by
# it on the very next draw. Measured on the module as it stood: Y1=5, Zoom Fit,
# ZeroDivisionError inside the draw handler -- the graph stopped painting, from
# two keystrokes and a button.
for expression in ("5", "0", "X-X", "-3.5"):
    app.ys = [expression, "", "", ""]
    app.y_enabled = [True, False, False, False]
    app.window.update(xmin=-10., xmax=10., ymin=-10., ymax=10.,
                      xscl=1., yscl=1.)
    app._zoom("fit")
    span = app.window["ymax"] - app.window["ymin"]
    try:
        app._draw_graph(_Area(), cr)
        drew = True
    except Exception as exc:
        drew = "%s: %s" % (type(exc).__name__, exc)
    check("Zoom Fit on the constant %s leaves a window with height" % expression,
          span > 0, "span %r" % span)
    check("...and the graph still draws", drew is True, drew)

# ------------------------------------------- a typed window is screened for NaN
# window_is_valid was lifted out of the Window dialog to be checkable at all:
# inside the dialog it sat behind a `dialog.run()` no test can drive. The bug it
# was missing is the reason it is worth having out here -- every ordering
# comparison is False when either side is NaN, so an all-NaN window satisfied
# all four rules and the next draw died with "cannot convert float NaN to
# integer". float() accepts "nan" and "inf" without complaint.
GOOD = dict(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)
NAN, INF = float("nan"), float("inf")
check("a sane window is accepted", calculator.window_is_valid(GOOD))
for name, w in (("all NaN", dict(xmin=NAN, xmax=NAN, ymin=NAN, ymax=NAN,
                                 xscl=NAN, yscl=NAN)),
                ("one NaN bound", dict(GOOD, ymin=NAN)),
                ("an infinite bound", dict(GOOD, xmax=INF)),
                ("a flat y range", dict(GOOD, ymin=5., ymax=5.)),
                ("reversed x bounds", dict(GOOD, xmin=10., xmax=-10.)),
                ("a zero x scale", dict(GOOD, xscl=0.)),
                ("a window missing its keys", {"xmin": -1.})):
    check("%s is refused" % name, not calculator.window_is_valid(w), w)

# --------------------------------------------- the trace cannot leave the graph
# Held down, Left/Right used to walk trace_x off the window and keep going: 400
# presses from x=0 in a [-10, 10] window put it at x=80 -- pixel 4050 of a 900px
# canvas -- while the readout confidently reported "X=80 Y=0.984807753012" for a
# point nobody could see. trace_x is PERSISTED, so closing the app did not
# recover it; it reopened just as lost.
from gi.repository import Gdk                                  # noqa: E402


def _arrow(name):
    return type("Event", (), {"keyval": Gdk.keyval_from_name(name)})()


app.window.update(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)
app.ys = ["sin(X)", "", "", ""]
app.y_enabled = [True, False, False, False]
app.trace_x = 0.0
for _ in range(400):
    app._on_graph_key(None, _arrow("Right"))
check("holding Right stops the trace at the right edge",
      app.trace_x == app.window["xmax"], app.trace_x)
for _ in range(800):
    app._on_graph_key(None, _arrow("Left"))
check("holding Left stops it at the left edge",
      app.trace_x == app.window["xmin"], app.trace_x)

# ...and a trace already saved off the edge comes back, rather than reopening
# in the same lost place.
_lost = calculator.sanitize_state({
    "trace_x": 999.0,
    "window": {"xmin": -10., "xmax": 10., "ymin": -10., "ymax": 10.,
               "xscl": 1., "yscl": 1.}})
check("a trace saved off the edge is brought back on load",
      _lost["trace_x"] == 10.0, _lost["trace_x"])

# The transforms themselves are exact and stay that way: tracing reads a value
# back out of a pixel, so a drift here would put the readout on the wrong point.
_W = dict(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)
_worst = 0.0
for _x in (-10, -3.7, 0, 1, 9.99, 10):
    for _y in (-10, -0.5, 0, 4.25, 10):
        _px, _py = calculator.graph_to_pixel(_x, _y, _W, 900, 500)
        _bx, _by = calculator.pixel_to_graph(_px, _py, _W, 900, 500)
        _worst = max(_worst, abs(_bx - _x), abs(_by - _y))
check("pixel and graph coordinates round-trip exactly",
      _worst < 1e-9, "worst error %.3g" % _worst)

# ------------------------------------- a typed Table Step is screened too
# Same class as the window, and it was equally unguarded. Measured on the module
# as it stood, with the table showing Y1 = X:
#     Step = 0      all 40 rows read x = 0
#     Step = nan    every x was nan
#     Step = inf    every x was inf, and the first was nan
# A step of zero is the interesting one: a plausible typo, accepted in silence,
# producing a table that looks broken with nothing to say why.


class _Entry(object):
    """A stand-in for the Table Start / Table Step field.

    It grew set_text when the app started PUTTING THE LIVE VALUE BACK after
    refusing what was typed: left as typed, the field read "0" or "abc" over a
    table plainly stepping by 1, and contradicted itself in silence."""

    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text


for typed, keep in (("2", 2.0), ("-1", -1.0), ("0.25", 0.25)):
    app.tbl_step = 1.0
    app._table_setting(_Entry(typed), "tbl_step")
    check("a Table Step of %s is taken" % typed, app.tbl_step == keep,
          app.tbl_step)

for typed in ("0", "nan", "inf", "-inf", "1e400", "abc", ""):
    app.tbl_step = 7.0
    field = _Entry(typed)
    app._table_setting(field, "tbl_step")
    check("a Table Step of %r leaves the old one alone" % typed,
          app.tbl_step == 7.0, app.tbl_step)
    check("...and the field says so, not %r" % typed,
          field.get_text() == calculator.format_number(7.0),
          field.get_text())

check("a stored Table Step of zero is not loaded",
      calculator.sanitize_state({"tbl_step": 0.0})["tbl_step"] == 1.0,
      calculator.sanitize_state({"tbl_step": 0.0})["tbl_step"])
check("...and neither is a stored NaN",
      calculator.sanitize_state({"tbl_step": float("nan")})["tbl_step"] == 1.0,
      calculator.sanitize_state({"tbl_step": float("nan")})["tbl_step"])

# ------------------------------------------------- the three zooms do their jobs
# Found by the mutation sweep, not by reading: swapping `kind == "in"`,
# `kind == "fit"` and `kind == "standard"` to `!=` all SURVIVED every suite.
# Zoom Fit was covered (its flat-curve padding); the other three were not
# covered at all, so any of them could have been rewired silently.
_BASE = dict(xmin=-4., xmax=6., ymin=-2., ymax=8., xscl=1., yscl=1.)


def _win():
    return tuple(round(app.window[k], 6) for k in ("xmin", "xmax", "ymin", "ymax"))


app.window.update(_BASE)
app._zoom("in")
check("Zoom In halves the window about its centre",
      _win() == (-1.5, 3.5, 0.5, 5.5), _win())

app.window.update(_BASE)
app._zoom("out")
check("Zoom Out doubles it about its centre",
      _win() == (-9.0, 11.0, -7.0, 13.0), _win())

app.window.update(_BASE)
app._zoom("standard")
check("Zoom Standard returns to -10..10 on both axes",
      _win() == (-10.0, 10.0, -10.0, 10.0), _win())

# ------------------------------------------- Up/Down walk the ENABLED curves
# The sweep survived a swap in the curve-cycling arithmetic too. Tracing must
# step between the curves that are actually drawn, and skip the ones that are
# not -- landing the readout on a curve nobody can see is the same fault as
# landing it off the edge of the graph.
app.ys = ["X", "X^2", "", "1/X"]
app.y_enabled = [True, True, False, True]
app.trace_curve = 0
_down = []
for _ in range(6):
    app._on_graph_key(None, _arrow("Down"))
    _down.append(app.trace_curve)
check("Down cycles through the enabled curves only",
      _down == [1, 3, 0, 1, 3, 0], _down)

app.trace_curve = 0
_up = []
for _ in range(4):
    app._on_graph_key(None, _arrow("Up"))
    _up.append(app.trace_curve)
check("...and Up cycles the other way",
      _up == [3, 1, 0, 3], _up)

# ------------------------------------------- the table columns follow the same list
app.ys = ["X", "X^2", "", "1/X"]
app.y_enabled = [True, False, False, True]
app._refresh_table()
_hdr = {}
for _kid in app.table_grid.get_children():
    if app.table_grid.child_get_property(_kid, "top-attach") == 0:
        _hdr[app.table_grid.child_get_property(_kid, "left-attach")] = _kid.get_text()
check("the table shows a column for each enabled function and no others",
      [_hdr[i] for i in sorted(_hdr)] == ["X", "Y1", "Y4"],
      [_hdr[i] for i in sorted(_hdr)])

# --------------------------------------------- the curve is sampled where it should be
# Found by the ARITHMETIC sweep: `dx = (xmax - xmin) / max(1, samples - 1)` with
# the minus flipped SURVIVED every suite. Nothing checked where the samples
# actually land — only that a curve drew at all — so the whole plot could shift
# or compress and every check would stay green.
_segs = calculator.sample_segments(lambda x: x, -10, 10, 21)
_xs = [x for _s in _segs for x, _y in _s]
check("a curve is sampled from one end of the window to the other",
      _xs and abs(_xs[0] - (-10)) < 1e-9 and abs(_xs[-1] - 10) < 1e-9,
      (_xs[:1], _xs[-1:]))
check("...at an even step across it",
      len(_xs) == 21 and abs((_xs[1] - _xs[0]) - 1.0) < 1e-9,
      (len(_xs), _xs[1] - _xs[0] if len(_xs) > 1 else None))

# ------------------------------------------- the trace readout names the right curve
# `self.trace_curve + 1` with the plus flipped also survived: the readout would
# say Y0 while tracing Y1. An off-by-one in a LABEL is the kind of thing nobody
# writes a check for and everybody notices.
app.ys = ["X", "X^2", "", "1/X"]
app.y_enabled = [True, True, False, True]
app.trace_x = 2.0
app.trace_curve = 0
app._update_trace()
check("tracing the first curve says Y1",
      app.trace_label.get_text().startswith("Y1"), app.trace_label.get_text())
app.trace_curve = 3
app._update_trace()
check("...and the fourth says Y4",
      app.trace_label.get_text().startswith("Y4"), app.trace_label.get_text())

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
