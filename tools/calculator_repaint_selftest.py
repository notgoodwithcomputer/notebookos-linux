#!/usr/bin/env python3
"""The graph page paints once and stops asking for another.

    tools/guestrun.sh python3 tools/calculator_repaint_selftest.py

`_draw_graph` ended with `self._update_trace()`, and `_update_trace` ended with
`self.trace_label.set_text(trace)`. GTK's `gtk_label_set_text` does not compare
before it writes: it sets the text and queues a resize whether or not anything
changed. So the paint asked for the next paint, on every frame, for ever.

WHAT THAT COST, measured three ways, because the answer is different in each
and saying so is the point:

    a mapped toplevel on X          1 draw, then idle
    a plain Gtk.OffscreenWindow     1 draw, then idle
    the offscreen holder that       601 draws, 25.4 seconds of CPU, and every
    tools/appdrive.py builds        one of them produced the SAME readout
    (a _PanelClamp inside an        text: "Y1  X=0  Y=0"
    EventBox, held at 1024x740)

So this is NOT a freeze a person meets on the guest -- the frame clock of a
real mapped window absorbs it -- and the report must not claim one. What it
IS: the Graph page could not be driven at all by the instrument this OS uses
to find defects by real use. Putting two curves on the graph under appdrive
hung the drive; every check downstream of it went unrun. A paint that writes
to a widget is a paint that cannot be trusted to end, and here it made the
page untestable by the one tool that would have found what else was wrong
with it (it had two other defects in the readout -- see
calculator_trace_selftest.py -- and neither could be reached until this was
fixed).

THE FIX is the pattern this app already uses for its keypad faces
(`_apply_face` / `_apply_tip`): remember what was last written and skip the
call when it has not changed. The first paint after a real change still
writes, so the readout is never stale; the paint after it writes nothing, so
no further paint is asked for.

RED PROOF (M1), the three-line guard removed from `_update_trace` on a scratch
copy, this suite pointed at it with CALCULATOR_MODULE_DIR. MEASURED:

    FAIL a second identical paint writes nothing to the trace readout
         <- set_text called 1 time(s) on the second paint
    FAIL ...and the page then settles instead of asking for another paint
         <- 21 draws in 60 turns of the main loop, still pending: True
    2 checks failed

Both halves are here on purpose. The first is the mechanism and cannot be
flaky. The second is the consequence that mattered, in the exact holder it
mattered in -- and it is bounded at both ends (a turn count and a draw cap),
because a check that HANGS on the broken code is not a check, it is a gate
that will one day stop a whole run with no verdict.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="calc-repaint-")
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import cairo                                                  # noqa: E402
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


def settle(limit=60):
    """Turn the main loop until nothing is pending, or `limit` turns -- and
    say how many turns it took.

    BOUNDED ON PURPOSE. On the unfixed module the graph page never stops
    queueing frames, so the ordinary `while Gtk.events_pending()` drain IS the
    hang. Turning a fixed number of times and reporting the count turns the
    same fact into a red line instead of a run that never finishes."""
    turns = 0
    while turns < limit and Gtk.events_pending():
        Gtk.main_iteration_do(False)
        turns += 1
    return turns


app = calculator.Calculator()
settle()

# --------------------------------------------------- the mechanism, in isolation
# Two curves and a trace, exactly the state the page would not settle in. Paint
# it twice with NOTHING changed in between; the second paint must write nothing.
app.ys = ["X-X^2/8", "2*sin(X)", "", ""]
app.y_enabled = [True, True, False, False]
app.trace_curve = 0
app.trace_x = 0.0

writes = []
real_set_text = app.trace_label.set_text
app.trace_label.set_text = lambda text: (writes.append(text),
                                         real_set_text(text))[1]


def paint():
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 400)
    app._draw_graph(app.graph, cairo.Context(surface))


paint()                                  # the first paint may write: it is new
writes[:] = []
paint()                                  # the second has nothing new to say
check("a second identical paint writes nothing to the trace readout",
      not writes, "set_text called %d time(s) on the second paint" % len(writes))
check("...and the readout still says what it should",
      app.trace_label.get_text().startswith("Y1")
      and "X=0" in app.trace_label.get_text(), app.trace_label.get_text())
writes[:] = []
app.trace_x = 3.0
paint()
check("...and a real change is still written", bool(writes), writes)
app.trace_label.set_text = real_set_text

# ------------------------------------ and in the holder the real-use driver uses
# The same shape tools/appdrive.py builds: the app's tree inside an EventBox
# inside uishot._PanelClamp, held at the smallest panel the OS supports, in an
# OffscreenWindow. A plain OffscreenWindow does NOT reproduce this and neither
# does a mapped toplevel -- which is why the holder is spelled out here rather
# than simplified to whichever one is shorter to write.
draws = {"n": 0}
real_update = calculator.Calculator._update_trace


def counted(self):
    draws["n"] += 1
    return real_update(self)


# `_update_trace` runs exactly once per `_draw_graph` and is looked up on the
# instance every time, so counting there needs no re-connection of the real
# draw handler (which was bound at connect time and cannot be swapped out).
calculator.Calculator._update_trace = counted

# A calculator of its own: the section above painted through a cairo context of
# its own making and left the app half-driven, and a holder measurement has to
# start from a window nobody has touched.
app = calculator.Calculator()
child = app.get_child()
app.remove(child)
holder = Gtk.OffscreenWindow()
holder.set_size_request(1024, 740)
background = Gtk.EventBox()
background.add(child)
clamp = uishot._PanelClamp(1024, 740)
clamp.add(background)
holder.add(clamp)
holder.show_all()
settle()
app._switch_view("graph")
settle()

app.y_entries[0].set_text("X-X^2/8")
app.y_entries[1].set_text("2*sin(X)")
app.y_checks[1].set_active(True)
draws["n"] = 0
turns = settle()
# ...and then a real paint, the way appdrive's shot() takes one: synchronously,
# through the holder's own draw. Without this a fixed module settles at ZERO
# draws and the check cannot tell "stopped asking" from "never drew at all".
holder.check_resize()                    # a synchronous draw needs its layout
settle()
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1024, 740)
clamp.draw(cairo.Context(surface))
painted = draws["n"]
turns += settle()
pending = Gtk.events_pending()
calculator.Calculator._update_trace = real_update
check("the graph is painted in the holder appdrive drives it in",
      painted >= 1, "the drive never drew the graph at all")
check("...and the page then settles instead of asking for another paint",
      not pending and draws["n"] <= 8,
      "%d draws in %d turns of the main loop, still pending: %s"
      % (draws["n"], turns, bool(pending)))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
