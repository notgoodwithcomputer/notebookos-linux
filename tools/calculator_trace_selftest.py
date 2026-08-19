#!/usr/bin/env python3
"""The line under the graph describes the graph.

    tools/guestrun.sh python3 tools/calculator_trace_selftest.py

TWO defects, both found by looking at the picture and reading the caption
under it, and both the same defect the trace RING was added to fix -- "Left
and Right moved a point the readout described and nothing showed" -- arriving
through two doors nobody had shut.

1 -- THE READOUT DESCRIBED A CURVE THAT WAS NOT ON THE GRAPH. Untick every
function. The canvas is empty, no ring is drawn (`trace_point()` correctly
returns None), and the line under it still reads

        Y1  X=8.07228915663  Y=6.51618522282

a value, to twelve figures, for something nobody plotted and nothing drew.
`_update_trace` evaluated `ys[trace_curve]` straight, with no regard for
whether that curve was enabled or even had an expression in it. Two readers of
"where is the trace", one of them knowing the rule and the other not.

2 -- ZOOM STRANDED THE TRACE. `_on_graph_key` clamps every arrow press into
the window, and says why: "a trace that cannot leave the graph cannot get lost
on it". But the WINDOW moves as well. Click near the right edge of a standard
window (X=8.07), press Zoom In, and the graph runs -5..5 while the readout
goes on reporting X=8.07 with the ring off the edge. trace_x is persisted, so
it reopens exactly as lost. The Window dialog's Apply had the same hole.

Both are now one rule in one place -- `_clamp_trace()`, called from the click,
the arrow keys, every zoom and the Window dialog -- and one source of truth for
the value, `trace_point()`, read by the ring and the caption alike.

RED PROOFS (M1..M3), each applied ALONE to a scratch copy, this suite pointed
at it with CALCULATOR_MODULE_DIR. All three MEASURED:

  1. the readout evaluates ys[] directly again
     (`point = self.trace_point()` -> the old try/except around _eval_x)
                                                                 2 FAILED
       FAIL the readout says nothing is there when no curve is ticked
            <- 'Y1  X=8.07228915663  Y=6.51618522282'
       FAIL ...and when the traced curve's box is empty
            <- 'Y2  X=3  Y=0'   (an empty box evaluates to nothing,
                                 and nothing formatted is a
                                 confident zero)

  2. Zoom stops clamping the trace
     (`self._clamp_trace()` removed from `_zoom`)                 3 FAILED
       FAIL Zoom In keeps the trace on the graph        <- X=8.07 in [-5, 5]
       FAIL ...and the readout follows it               <- Y1  X=8.07228915663
       FAIL ...from the other edge too                  <- X=-9.5 in [-5, 5]
     Zoom FIT stays green under this one, on purpose: it moves only the y
     bounds, so it cannot strand a trace by itself. It is checked anyway
     because the next person to make it move x will find out here.

  3. Apply stops clamping it
     (`self._clamp_trace()` removed from `_apply_window`)         2 FAILED
       FAIL a hand-typed window keeps the trace on the graph  <- X=8.07 in [-2, 2]
       FAIL ...and the readout follows a hand-typed window too

"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="calc-trace-")
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402

uishot.load_theme()
nbapp.screen_size = lambda: (1024, 740)
import calculator                                             # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump():
    for _ in range(4):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


app = calculator.Calculator()
pump()

STANDARD = dict(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)


def standard():
    app.window.update(STANDARD)


# ------------------------------------------- 1: the caption and the picture agree
app.ys = ["X^2/10", "", "", ""]
app.y_enabled = [True, False, False, False]
app.trace_curve = 0
app.trace_x = 8.07228915663
standard()
app._update_trace()
check("the readout reports the traced value while the curve is on",
      app.trace_label.get_text() == "Y1  X=8.07228915663  Y=6.51618522282",
      app.trace_label.get_text())
check("...and the ring has somewhere to be drawn",
      app.trace_point() is not None, app.trace_point())

app.y_enabled[0] = False
app._update_trace()
check("the readout says nothing is there when no curve is ticked",
      app.trace_point() is None
      and app.trace_label.get_text().endswith(calculator._t("Undefined")),
      app.trace_label.get_text())

app.ys = ["X^2/10", "", "", ""]
app.y_enabled = [True, True, False, False]     # Y2 ticked with an EMPTY box
app.trace_curve = 1
app.trace_x = 3.0
app._update_trace()
check("...and when the traced curve's box is empty",
      app.trace_point() is None
      and app.trace_label.get_text().endswith(calculator._t("Undefined")),
      app.trace_label.get_text())

# ...and it must still be able to say a real number, or the fix above is just
# a readout that never says anything.
app.ys[1] = "X^2"
app._update_trace()
check("a ticked curve with an expression still reads out its value",
      app.trace_label.get_text() == "Y2  X=3  Y=9", app.trace_label.get_text())

# ------------------------------------------------- 2: the trace stays on the graph
app.ys = ["X^2/10", "", "", ""]
app.y_enabled = [True, False, False, False]
app.trace_curve = 0
standard()
app.trace_x = 8.07228915663
app._zoom("in")
check("Zoom In keeps the trace on the graph",
      app.window["xmin"] <= app.trace_x <= app.window["xmax"],
      "X=%s in [%s, %s]" % (app.trace_x, app.window["xmin"], app.window["xmax"]))
check("...and the readout follows it",
      app.trace_label.get_text() == "Y1  X=5  Y=2.5", app.trace_label.get_text())

standard()
app.trace_x = -9.5
app._zoom("in")
check("...from the other edge too",
      app.window["xmin"] <= app.trace_x <= app.window["xmax"],
      "X=%s in [%s, %s]" % (app.trace_x, app.window["xmin"], app.window["xmax"]))

standard()
app.trace_x = 9.0
app._zoom("fit")
check("Zoom Fit keeps the trace on the graph",
      app.window["xmin"] <= app.trace_x <= app.window["xmax"],
      "X=%s in [%s, %s]" % (app.trace_x, app.window["xmin"], app.window["xmax"]))

# Zooming OUT must not drag the trace anywhere: it was already inside.
standard()
app.trace_x = 4.0
app._zoom("out")
check("Zoom Out leaves a trace that was already on the graph alone",
      app.trace_x == 4.0, app.trace_x)

# ------------------------------------------------- 3: a hand-typed window too
# Everything else in the Window dialog sits behind a `dialog.run()` that no
# test can drive, so Apply's own work was lifted into `_apply_window` -- the
# one thing the dialog calls once it has a window it accepts. Driving that is
# driving Apply.
standard()
app.trace_x = 8.07228915663
app._apply_window(dict(xmin=-2., xmax=2., ymin=-2., ymax=2., xscl=1., yscl=1.))
check("a hand-typed window keeps the trace on the graph",
      app.window["xmin"] <= app.trace_x <= app.window["xmax"],
      "X=%s in [%s, %s]" % (app.trace_x, app.window["xmin"], app.window["xmax"]))
check("...and the readout follows a hand-typed window too",
      app.trace_label.get_text() == "Y1  X=2  Y=0.4", app.trace_label.get_text())
import inspect                                                # noqa: E402
check("...and Apply is what runs it",
      "_apply_window(values)" in inspect.getsource(
          calculator.Calculator._window_dialog),
      "the dialog does not call _apply_window")

# A stored trace is clamped on the way in as well (the load-side half, which
# already shipped: this keeps the two halves from drifting apart).
loaded = calculator.sanitize_state({"trace_x": 999.0,
                                    "window": dict(STANDARD)})
check("a stored trace off the edge comes back on screen",
      loaded["trace_x"] == 10.0, loaded["trace_x"])

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
