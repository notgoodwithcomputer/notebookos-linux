#!/usr/bin/env python3
"""Real-use regression drive for the Calculator, on the real widget tree.

Every check below is a thing a person did with the calculator and got a wrong
or dishonest answer for, driven the same way they did it (typing on the
keyboard, the View menu, the Graph and Table pages, the Window dialog) through
tools/appdrive on an offscreen holder. Each check is named, and fails by name
rather than by crash, so a missing method reads as the defect it is.

  letter after an answer     7×7= then A+B answered 245: the key ladder gave
                             every non-digit the operator key type, so a typed
                             letter was APPENDED to the answer ("49A+B") while
                             the keypad's own π key correctly began afresh
  graph arrows               Up/Down on the focused graph walked the home tape
                             instead of switching the traced curve, because the
                             window-level key handler runs before the canvas
  Escape leaves the page     Escape on Graph/Table closed the whole calculator:
                             nbapp connects AppWindow._on_key first, so an
                             Escape branch further down was unreachable
  Fix mode covers integers   in Fix 2, 10÷2 read 5.00 and 2+2 read 4
  an error is not a 0        after 10÷0, a mode key left "10÷0 =" over a
                             placeholder 0 — a false reading of the failure
  typed function names       sin(30) typed on the keyboard became SIN(30) and
                             failed; only the paste path knew the names
  float noise                sin(π) in radians read 1.22464679915e-16, and
                             0.1+0.2−0.3 read 5.55111512313e-17
  table fields               a refused ("0", "abc") or unapplied value stayed
                             in the field, contradicting the table beside it
  the trace has a cursor     the readout described a point nothing marked, on
                             a graph whose axes carried no numbers
  window dialog              Apply silently dropped an impossible window
  menu promises              "Float" named a state, not an action, and Paste
                             was bound to Ctrl+V with no menu entry at all

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \
      tools/calculator_realuse_selftest.py

Exit status is the number of failed checks.
"""
import os
import sys
import math
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="calc-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                   # noqa: E402
import cairo                                                      # noqa: E402
from gi.repository import Gtk, Gdk                                # noqa: E402

FAILED = []
SHOTS = os.environ.get("CALC_REALUSE_SHOTS", "")


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    print(("PASS " if ok else "FAIL ") + name +
          (("  -- " + str(detail)) if (detail and not ok) else ""))
    return ok


def shot(d, base, note=""):
    if SHOTS:
        d.shot(os.path.join(SHOTS, base), note)


def fresh():
    shutil.rmtree(os.path.join(HOME_ROOT, "calculator"), ignore_errors=True)
    d = appdrive.Drive("calculator")
    # HARNESS WORKAROUND, not app behaviour: an offscreen holder has no frame
    # clock, so the trace label set from inside the graph's draw handler
    # queues a resize that repaints, redraws, and sets it again forever.
    # Setting it only when it actually changes breaks that loop.
    lbl = getattr(d.app, "trace_label", None)
    if lbl is not None:
        original = lbl.set_text

        def once(text, _o=original, _l=lbl):
            if _l.get_text() != text:
                _o(text)
        lbl.set_text = once
    return d


KEYS = {"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
        "6": "6", "7": "7", "8": "8", "9": "9", ".": "period",
        "+": ("plus", True), "-": "minus", "*": ("asterisk", True),
        "/": ("slash", True), "(": ("parenleft", True),
        ")": ("parenright", True), "=": "Return", "%": ("percent", True),
        "^": ("asciicircum", True)}


def typing(d, text):
    """Type `text` on a real keyboard, one key at a time."""
    for ch in text:
        spec = KEYS.get(ch)
        if spec is None:                       # a letter
            d.key(ch)
        elif isinstance(spec, tuple):
            d.key(spec[0], shift=spec[1])
        else:
            d.key(spec)


def disp(d):
    return d.app.disp_lbl.get_text().strip()


def hist(d):
    return d.app.hist_lbl.get_text().strip()


def clear(d):
    d.key("Delete")


def set_fix(app, value):
    """The Display Mode dialog's effect, however this build applies it."""
    if hasattr(app, "_set_fix"):
        app._set_fix(value)
    else:                                       # older build: the field itself
        app.fix = value
        app._refresh()


def labels(d, menu):
    out = []
    for it in d.menu(menu):
        if isinstance(it, (tuple, list)) and isinstance(it[0], str):
            out.append(it[0])
    return out


# ---------------------------------------------------------------- home page
d = fresh()

# CALC-1 — a typed letter after "=" starts a new expression, exactly as a
# typed digit and the keypad's own π key already did.
typing(d, "7*7=")
check("7×7= answers 49", disp(d) == "49", repr(disp(d)))
d.key("a")
check("a typed variable after an answer starts a new expression",
      d.app.expr == "A", "expr %r display %r" % (d.app.expr, disp(d)))
clear(d)
d.app.variables["A"] = 4.0
d.app.variables["B"] = 49.0
typing(d, "7*7=")
typing(d, "a+b=")
check("A+B typed after an answer computes A+B",
      disp(d) == "53", "history %r display %r" % (hist(d), disp(d)))
shot(d, "r01-letter-after-answer.png", "A+B after 7×7=")

# ...and an operator typed after "=" still continues the answer.
clear(d)
typing(d, "7*7=")
typing(d, "+1=")
check("an operator typed after an answer continues it", disp(d) == "50",
      "history %r display %r" % (hist(d), disp(d)))

# CALC-4 — the chosen display mode applies to every answer, not only the ones
# that happen to come out fractional.
set_fix(d.app, 2)
clear(d)
typing(d, "10/2=")
half = disp(d)
clear(d)
typing(d, "2+2=")
check("Fix 2 shows a whole-number answer with two decimals",
      disp(d) == "4.00", "10÷2 gave %r, 2+2 gave %r" % (half, disp(d)))
shot(d, "r02-fix2.png", "Fix 2: 2+2")
set_fix(d.app, None)
clear(d)
typing(d, "2+2=")
check("Float mode still shows a whole number whole", disp(d) == "4",
      repr(disp(d)))

# CALC-5 — a failed calculation is not quietly replaced by a 0 under its own
# history line when a mode key is pressed.
clear(d)
typing(d, "10/0=")
check("10÷0 says why it cannot be answered",
      d.app.error and "0" != disp(d), "display %r" % disp(d))
before = hist(d)
d.app.press(("DEG", "deg", None, "fn"))
d.pump(0.05)
check("a mode key does not leave a failed calculation over a 0",
      not (disp(d) == "0" and hist(d) == before),
      "history %r over display %r" % (hist(d), disp(d)))
shot(d, "r03-error-then-deg.png", "10÷0 then DEG")
d.app.press(("DEG", "deg", None, "fn"))     # back to degrees

# CALC-6 — the function names the keypad and the catalog insert can be TYPED.
clear(d)
typing(d, "sin(30)=")
check("a function name typed on the keyboard is computed",
      disp(d) == "0.5", "history %r display %r" % (hist(d), disp(d)))
check("a typed function name is shown the way the keypad spells it",
      hist(d).startswith("sin(30"), repr(hist(d)))
shot(d, "r04-typed-sin.png", "typed sin(30)=")
clear(d)
typing(d, "sqrt(16)=")
check("sqrt typed on the keyboard is computed", disp(d) == "4", repr(disp(d)))
clear(d)
d.app.variables["X"] = 3.0
d.key("x")
check("a single letter is still a variable, not a function name",
      d.app.expr == "X", repr(d.app.expr))

# CALC-7 — float noise never reaches the display.
clear(d)
d.app._set_deg(False)
d.app.press(("sin", "app", "sin(", "fn"))
d.app.press(("π", "app", "π", "fn"))
d.key("Return")
check("sin(π) in radians is 0", disp(d) == "0", repr(disp(d)))
clear(d)
d.app.press(("cos", "app", "cos(", "fn"))
d.app.press(("π", "app", "π", "fn"))
typing(d, "/2=")
check("cos(π÷2) in radians is 0", disp(d) == "0", repr(disp(d)))
d.app._set_deg(True)
clear(d)
typing(d, "0.1+0.2-0.3=")
check("0.1+0.2−0.3 is 0", disp(d) == "0", repr(disp(d)))
clear(d)
typing(d, "1/10^20=")
check("a genuinely tiny answer is still shown",
      disp(d) not in ("0", "0.00"), repr(disp(d)))

# CALC-10 / CALC-12 — what the menus promise.
view = labels(d, "View")
check("the View menu offers Display Mode with the ellipsis a dialog earns",
      any(lab.startswith("Display Mode…") for lab in view), view)
check("no View item is labelled with the current display mode instead",
      not any(lab.strip() in ("Float", "Fix 2") for lab in view), view)
edit = labels(d, "Edit")
check("the Edit menu offers Paste with the key it is bound to",
      any(lab.startswith("Paste") and "Ctrl+V" in lab for lab in edit), edit)
check("Copy Result shows its key too",
      any(lab.startswith("Copy Result") and "Ctrl+C" in lab for lab in edit),
      edit)
clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
clip.set_text("3*(4+5)", -1)
d.pump(0.2)
clear(d)
try:
    d.menu_action("Edit", "Paste")
    d.pump(0.4)
    pasted = d.app.expr
except LookupError as exc:
    pasted = "no such menu item (%s)" % exc
# ...spelled the way the KEYPAD spells it. A paste used to be normalised the
# other way -- towards Python's * and / -- and the display then carried two
# multiplication signs in one expression: paste "3*(4+5)", press the × key,
# and the readout said "3*(4+5)×2". The display is the keypad's own notation,
# and evaluate() reads either, so the paste is spelled to match what is on the
# keys.
check("the Edit menu's Paste brings an expression in",
      pasted == "3×(4+5)", repr(pasted))
d.close()

# --------------------------------------------------------------- graph page
d = fresh()
typing(d, "6/2=")
typing(d, "3+4=")
clear(d)
d.menu_action("View", "Graph")
app = d.app
app.y_entries[0].set_text("X")
app.y_checks[0].set_active(True)
app.y_entries[1].set_text("X^2/5")
app.y_checks[1].set_active(True)
app.graph.grab_focus()
d.pump(0.1)
home_expr = app.expr
d.key("Down")
d.pump(0.05)
check("Down on the focused graph switches the traced curve",
      app.trace_curve == 1, "trace_curve %r label %r"
      % (app.trace_curve, app.trace_label.get_text()))
app.trace_curve = 1          # so Up has somewhere to come back FROM even
d.key("Up")                  # when Down above never moved it
d.pump(0.05)
check("Up switches it back", app.trace_curve == 0,
      "trace_curve %r" % app.trace_curve)
check("arrows on the graph leave the home expression alone",
      app.expr == home_expr, "expression became %r" % app.expr)

# CALC-9 — the traced point is MARKED, and the axes carry numbers.
app.trace_curve = 0
app.window.update(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)


def ink_near(app, gx, gy, box=9):
    """Non-background pixels in a small box around a graph point."""
    w = max(app.graph.get_allocated_width(), 320)
    h = max(app.graph.get_allocated_height(), 240)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    app._draw_graph(app.graph, cr)
    surf.flush()
    data = surf.get_data()
    stride = surf.get_stride()
    px, py = to_pixel(gx, gy, app.window, w, h)
    n = 0
    for y in range(max(0, int(py) - box), min(h, int(py) + box)):
        row = y * stride
        for x in range(max(0, int(px) - box), min(w, int(px) + box)):
            b, g, r = data[row + x * 4], data[row + x * 4 + 1], data[row + x * 4 + 2]
            if abs(r - 250) + abs(g - 247) + abs(b - 240) > 40:
                n += 1
    return n


to_pixel = d.mod.graph_to_pixel
try:
    app.trace_x = 5.0
    with_marker = ink_near(app, 5.0, 5.0)
    app.trace_x = -5.0
    without = ink_near(app, 5.0, 5.0)
    marked = with_marker > without + 12
except Exception as exc:                                          # noqa: BLE001
    with_marker, without, marked = "-", "-", False
    print("   (marker check could not draw: %r)" % (exc,))
check("the traced point is marked on the graph itself", marked,
      "ink at the traced point %s vs %s elsewhere" % (with_marker, without))
app.trace_x = 5.0
d.pump(0.1)
shot(d, "r05-graph-trace.png", "trace marker at X=5")


class _TextSpy(object):
    """A cairo-shaped stand-in that records what text was drawn, and where."""

    class _Ext(object):
        width = 24.0
        height = 8.0

    def __init__(self):
        self.texts = []
        self.at = []
        self._pen = (0.0, 0.0)

    def move_to(self, x, y):
        self._pen = (x, y)

    def show_text(self, text):
        self.texts.append(text)
        self.at.append(self._pen)

    def text_extents(self, _text):
        return self._Ext()

    def __getattr__(self, _name):
        return lambda *a, **k: None


spy = _TextSpy()
try:
    app._draw_axis_numbers(spy, app.window, 640, 400)
    numbers = spy.texts
except Exception as exc:                                          # noqa: BLE001
    numbers = []
    print("   (axis numbers could not be drawn: %r)" % (exc,))
check("the graph says what its squares are worth",
      len(numbers) >= 4 and "5" in numbers, numbers)
# An axis number at the very edge of the window used to be drawn half off the
# canvas: "-10" appeared as "0", which is a different number.
whole = [(t, x) for t, (x, y) in zip(spy.texts, spy.at)
         if x < 0 or x + _TextSpy._Ext.width > 640 or y < 0 or y > 400]
check("an axis number at the edge of the graph is drawn whole",
      numbers and not whole, whole[:3])
try:
    step_near = d.app.axis_number_step(-5, 5, 1.0, 640)
    step_far = d.app.axis_number_step(-1000, 1000, 1.0, 640)
except Exception as exc:                                          # noqa: BLE001
    step_near, step_far = None, None
    print("   (axis number spacing could not be measured: %r)" % (exc,))
check("axis numbers thin out instead of crowding",
      step_near == 1.0 and (step_far or 0) > 1.0,
      "every %r over ten units, every %r over two thousand"
      % (step_near, step_far))

# CALC-11 — an impossible window is refused out loud, and the dialog stays up.
answers = []


def scripted(values, then):
    """Fill the Window dialog's fields and answer it, once per run() call."""
    original = Gtk.Dialog.run

    def run(self):
        entries = []

        def walk(w):
            if isinstance(w, Gtk.Entry):
                entries.append(w)
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)
        walk(self.get_content_area())
        entries.reverse()          # Grid children come back newest-first
        answers.append([e.get_style_context().has_class("error")
                        for e in entries])
        if len(answers) == 1:
            for e, v in zip(entries, values):
                if v is not None:
                    e.set_text(v)
            return Gtk.ResponseType.OK
        return then
    Gtk.Dialog.run = run
    return original


before = dict(app.window)
orig = scripted(["10", "-10", None, None, None, None],
                Gtk.ResponseType.CANCEL)
app._window_dialog()
Gtk.Dialog.run = orig
check("an impossible graph window keeps its dialog open",
      len(answers) >= 2, "the dialog was answered %d time(s)" % len(answers))
check("the refused fields are marked in the dialog",
      len(answers) >= 2 and any(answers[1][:2]),
      "error classes %r" % (answers[1:2],))
check("an impossible graph window is not applied", app.window == before,
      app.window)

answers[:] = []
orig = scripted(["-5", "5", None, None, None, None], Gtk.ResponseType.CANCEL)
app._window_dialog()
Gtk.Dialog.run = orig
check("a valid graph window applies and closes the dialog at once",
      app.window["xmin"] == -5.0 and app.window["xmax"] == 5.0
      and len(answers) == 1, "%r after %d run(s)" % (app.window, len(answers)))

# CALC-3 — Escape leaves the page, it does not leave the app.
d.key("Escape")
d.pump(0.1)
check("Escape on the graph page returns home",
      app.current_view == "home" and not getattr(app, "_closed", False),
      "view %r closed %r" % (app.current_view, getattr(app, "_closed", None)))
d.close()

# --------------------------------------------------------------- table page
d = fresh()
d.menu_action("View", "Table")
app = d.app
fields = [e for e in d.find(Gtk.Entry) if e.get_width_chars() == 8]
start_e, step_e = (fields + [None, None])[:2]
if step_e is None:
    check("the table page has its Start and Step fields", False,
          "%d field(s)" % len(fields))
else:
    step_e.grab_focus()
    step_e.set_text("")
    d.type("0")
    d.key("Return")
    d.pump(0.1)
    check("a refused table step puts the value the table uses back",
          step_e.get_text() == d.mod.format_number(app.tbl_step),
          "field %r while the table steps by %r"
          % (step_e.get_text(), app.tbl_step))
    start_e.grab_focus()
    start_e.set_text("")
    d.type("abc")
    d.key("Return")
    d.pump(0.1)
    check("a table start that is not a number is put back too",
          start_e.get_text() != "abc",
          "field %r while the table starts at %r"
          % (start_e.get_text(), app.tbl_start))
    step_e.grab_focus()
    step_e.set_text("")
    d.type("2")
    d.menu_action("View", "Graph")
    d.menu_action("View", "Table")
    d.pump(0.1)
    check("a table value typed and left is applied to the table",
          app.tbl_step == 2.0
          and step_e.get_text() == d.mod.format_number(app.tbl_step),
          "field %r while the table steps by %r"
          % (step_e.get_text(), app.tbl_step))
    shot(d, "r06-table-fields.png", "table fields after a refusal")

d.key("Escape")
d.pump(0.1)
check("Escape on the table page returns home",
      app.current_view == "home" and not getattr(app, "_closed", False),
      "view %r closed %r" % (app.current_view, getattr(app, "_closed", None)))
# ...and the base meaning of Escape survives the override: from home, with no
# page to leave, it still leaves the app.
d.key("Escape")
d.pump(0.1)
check("Escape on the home page still closes the calculator",
      getattr(app, "_closed", False), "closed %r" % getattr(app, "_closed", None))
d.close()

print("\n%d check(s) failed" % len(FAILED) if FAILED else "\nall checks passed")
for name in FAILED:
    print("  FAILED: " + name)
sys.exit(len(FAILED))
