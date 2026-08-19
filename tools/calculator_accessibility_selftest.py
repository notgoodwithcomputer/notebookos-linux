#!/usr/bin/env python3
"""Keyboard reach and naming in Calculator — in the source AND in the widgets.

    tools/guestrun.sh python3 tools/calculator_accessibility_selftest.py

This file was entirely source-grep: every check asked "is this string present in
calculator.py". That catches a deletion, which is worth something, but it cannot
see the thing it is named for — a grep passes just as happily when the widget it
describes is unreachable, unnamed, or never built. It also could not see any UI
added after it was written.

Measured with the widgets instead, and it found the gap the greps could not:
STO-> and MATH were the only two OPAQUE keys on the pad with no tooltip, and
they are the two that open a DIALOG — the keys most in need of saying what they
do. Every other non-obvious key (sqrt, x!, 1/x, pi, +/-, 2nd, AC, ...) had one.
They now reuse the wording of their own menu item, already translated in all 17
catalogs, so naming them cost no new strings.

A digit key needs no tooltip and is not asked for one: "7" says 7. The list of
self-evident faces is explicit below rather than implied, so a key that stops
being obvious cannot quietly join it.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALCULATOR_MODULE_DIR:

  1. the two dialog keys lose their tooltips again
     (the STO->/MATH entries removed from TOOLTIPS)                 1 FAILED
       FAIL every key that is not self-evident is named
            <- ['STO→', 'MATH']

  2. keys stop being focusable
     (`btn.set_relief(...)` line joined by `btn.set_can_focus(False)`)
                                                                    1 FAILED
       FAIL every key can be reached from the keyboard

  3. the history line stops being focusable
     (`self._histbox.set_can_focus(True)` -> `(False)`)             2 FAILED
       FAIL history target is keyboard focusable      <- the static grep
       FAIL the history line can actually be focused  <- the widget

  4. Return stops recalling from the history line
     (`if event.keyval in (...)` -> `if False and event.keyval in (...)`)
                                                                    1 FAILED
       FAIL pressing Return on the history line recalls the calculation
     The static "return, keypad enter and space recall history" check stays
     GREEN under this one: the key constants are still in the file. That pair
     is the whole argument for driving the widget.
"""
import os
import sys
from pathlib import Path

# $CALCULATOR_MODULE_DIR wins over the repo path. This suite reads the module
# as TEXT as well as importing it, so without it a red proof grades the PRISTINE
# file while believing it is grading the mutant -- a check must not read past
# its own subject.
_DE = os.environ.get("CALCULATOR_MODULE_DIR", str(
    Path(__file__).resolve().parents[1] /
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
text = (Path(_DE) / "calculator.py").read_text()
section = text[text.index("        self._histbox = Gtk.EventBox()"):
               text.index("    # ---- keypad ----")]

checks = {
    "full-allocation root carries opaque paper background":
        ('shell.get_style_context().add_class("calcroot")' in text
         and ".calcroot { background: #F8F7F2;" in text),
    "framebuffer-safe history target remains an EventBox": "Gtk.EventBox()" in section,
    "history target is keyboard focusable": "set_can_focus(True)" in section,
    "history action is described": "Recall last calculation" in section,
    "named keyboard handler is connected":
        'connect("key-press-event", self._on_history_key)' in section,
    "return, keypad enter and space recall history":
        all(k in section for k in ("Gdk.KEY_Return", "Gdk.KEY_KP_Enter", "Gdk.KEY_space")),
    "keyboard recall consumes handled input": "self.recall(-1)" in section and "return True" in section,
    "focus is visibly indicated": ".hist-box:focus" in text,
}

# ------------------------------------------------------------- the real widgets
import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-a11y-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, _DE)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk                            # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402

uishot.load_theme()
nbapp.screen_size = lambda: (1024, 722)
import calculator                                             # noqa: E402

app = calculator.Calculator()
_child = app.get_child()
app.remove(_child)
_off = Gtk.OffscreenWindow()
_off.set_size_request(1024, 722)
_off.add(_child)
_off.show_all()
for _ in range(40):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)

# A digit says what it is. Everything else on this pad is an abbreviation, a
# symbol or a mode, and has to be named. Listed rather than inferred so a face
# that stops being obvious cannot quietly join the exempt set.
SELF_EVIDENT = set("0123456789.") | {"+", "-", "−", "×", "÷", "="}
unnamed = [kd[0] for kd, btn, _f in app._buttons
           if kd[0] not in SELF_EVIDENT and not btn.get_tooltip_text()]
checks["every key that is not self-evident is named"] = (not unnamed, unnamed)

unreachable = [kd[0] for kd, btn, _f in app._buttons if not btn.get_can_focus()]
checks["every key can be reached from the keyboard"] = (not unreachable,
                                                        unreachable)

# An EMPTY history line is not a control: it is unfocusable and insensitive
# until there is a calculation to recall (it "disappears semantically" — see
# accessibility_ux_selftest), and becomes focusable the moment there is one.
checks["an empty history line is not offered to the keyboard"] = (
    not app._histbox.get_can_focus() and not app._histbox.get_sensitive())

# Drive the handler the way a keyboard does. The static check above only proves
# the key constants appear in the file.
app.press(("1", "app", "1", "num"))
app.press(("+", "app", "+", "op"))
app.press(("2", "app", "2", "num"))
app.press(("=", "eq", None, "eq"))
for _ in range(6):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
checks["the history line can actually be focused"] = (
    app._histbox.get_can_focus() and app._histbox.get_sensitive())
app.expr = ""
_ev = type("Event", (), {"keyval": Gdk.KEY_Return, "state": 0})()
_handled = app._on_history_key(app._histbox, _ev)
checks["pressing Return on the history line recalls the calculation"] = (
    bool(app.expr) and _handled is True, (app.expr, _handled))

# The damaged-store notice is dismissible, so its control must be reachable and
# named like any other.
_shut = [w for w in _off.get_children()]


def _walk(w, out=None):
    out = [] if out is None else out
    out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            _walk(c, out)
    return out


_dismiss = [w for w in _walk(_child)
            if isinstance(w, Gtk.Button)
            and w.get_style_context().has_class("damage-shut")]
checks["the damaged-store notice can be dismissed from the keyboard"] = (
    bool(_dismiss) and _dismiss[0].get_can_focus()
    and bool(_dismiss[0].get_tooltip_text()),
    [(b.get_can_focus(), b.get_tooltip_text()) for b in _dismiss])

ok = True
for name, passed in checks.items():
    detail = ""
    if isinstance(passed, tuple):
        passed, detail = passed
        detail = "" if passed else "\n     <- %s" % (detail,)
    print(("PASS " if passed else "FAIL ") + name + detail)
    ok &= bool(passed)
print("\n%d checks, %d failed" % (len(checks), sum(
    1 for v in checks.values()
    if not (v[0] if isinstance(v, tuple) else v))))
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
