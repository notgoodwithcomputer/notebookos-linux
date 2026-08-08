#!/usr/bin/env python3
"""
Typing on the calculator, and what "%" means.

`tools/func_coverage.py` reported 8 of calculator.py's 35 functions never
entered. Two of them mattered:

* **`_on_key_calc`** — the entire physical-keyboard path. `calculator_selftest`
  drives `evaluate()` directly, so nothing had ever typed on the thing, and on
  a laptop the keyboard is how a calculator is used.
* **`_operand_start`** — reached only by the RELATIVE percent rule. That it was
  never entered means `_PCT_REL` never matched in any test, so the whole reason
  `_expand_percent` exists ("200+10% is 220, not 200.1") was unverified. Its
  docstring makes three specific numeric claims; they are asserted here.

The defect in the gap: **`comma` and `KP_Separator` were not in the key table.**
A comma is the decimal point on most of the keyboard layouts this OS ships —
French, German, Spanish, Italian, Russian, Polish, Portuguese, Turkish — and on
a laptop with no numeric keypad the main-row comma is the only decimal key
there is. Typing "3,5" produced **35**.

Run:
    tools/guestrun.sh python3 tools/calculator_keyboard_selftest.py
    tools/guestrun.sh python3 tools/calculator_keyboard_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-calc-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

import calculator as C  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def press_key(app, name, ctrl=False):
    """One real key press through the real handler."""
    kv = Gdk.keyval_from_name(name)
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = kv
    ev.state = (Gdk.ModifierType.CONTROL_MASK if ctrl
                else Gdk.ModifierType(0))
    ev.string = ""
    ev.window = app.get_window()
    handled = app._on_key_calc(app, ev)
    pump()
    return handled


NAMES = {"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
         "6": "6", "7": "7", "8": "8", "9": "9", ".": "period",
         ",": "comma", "+": "plus", "-": "minus", "*": "asterisk",
         "/": "slash", "%": "percent", "^": "asciicircum", "!": "exclam",
         "(": "parenleft", ")": "parenright", "=": "equal"}


def type_it(app, text):
    for ch in text:
        press_key(app, NAMES[ch])


def shown(app):
    """The number on the display line — `disp_lbl`, and nothing else.

    Walking the whole window instead would search the menu bar, the clock and
    the mode chip, so "7" could be found in a timestamp and "0" in almost
    anything. The same shape as the bills suite reading the entire Gtk.Overlay
    when it wanted one card.
    """
    return app.disp_lbl.get_text()


def history(app):
    return app.hist_lbl.get_text()


def main():
    # ---- the relative percent rule, which nothing had ever reached --------
    for expr, want in (("200+10%", 220.0), ("200-10%", 180.0),
                       ("100+5%+5%", 110.25)):
        out = C._expand_percent(expr)
        try:
            got = eval(out, {"__builtins__": {}}, {})
        except Exception as exc:                                # noqa: BLE001
            got = "ERR %s" % type(exc).__name__
        check("%s is %g, not %s divided by a hundred" % (expr, want, expr[:3]),
              isinstance(got, float) and abs(got - want) < 1e-9,
              "%s = %r" % (out, got))
    # ...and everywhere else "%" still means N/100.
    for expr, want in (("50%", 0.5), ("200*10%", 20.0), ("10%*3", 0.3)):
        got = eval(C._expand_percent(expr), {"__builtins__": {}}, {})
        check("%s keeps the plain meaning (%g)" % (expr, want),
              abs(got - want) < 1e-9, repr(got))
    # A percentage inside a group takes the group's own left-hand value, not
    # something from outside it — the reason _operand_start walks back over
    # balanced parentheses.
    got = eval(C._expand_percent("2*(3+10%)"), {"__builtins__": {}}, {})
    check("a percentage inside a group uses that group's value (6.6)",
          abs(got - 6.6) < 1e-9, repr(got))

    if not Gtk.init_check()[0]:
        print("SKIP GTK interaction checks: no display connection")
        print("RESULT: %s" % ("ALL PASS" if not FAILED else "FAILED"))
        return 1 if FAILED else 0

    app = C.Calculator()
    pump()

    # ---- typing an expression --------------------------------------------
    press_key(app, "Delete")            # AC
    handled = press_key(app, "2")
    check("a digit key is claimed by the calculator", handled is True)
    type_it(app, "+3=")
    check("typing 2+3= gives 5", shown(app).strip() == "5", repr(shown(app)))

    # ---- THE DEFECT: a comma is a decimal point on most layouts ----------
    press_key(app, "Delete")
    took = press_key(app, "comma")
    check("the comma key is not dead", took is True, repr(took))
    press_key(app, "Delete")
    type_it(app, "3,5*2=")
    got = shown(app).strip()
    check("typing 3,5*2= gives 7, not 70", got == "7", repr(got))
    # The numeric-keypad separator is the same key on those layouts.
    press_key(app, "Delete")
    check("KP_Separator is not dead either",
          press_key(app, "KP_Separator") is True)

    # ...and the period still works, for the layouts that have one.
    press_key(app, "Delete")
    type_it(app, "3.5*2=")
    check("the period key still works", shown(app).strip() == "7",
          repr(shown(app)))

    # ---- the keypad names that are not plain characters -------------------
    press_key(app, "Delete")
    type_it(app, "12")
    press_key(app, "BackSpace")
    press_key(app, "equal")
    check("BackSpace deletes one keystroke", shown(app).strip() == "1",
          repr(shown(app)))
    press_key(app, "Delete")
    check("Delete clears everything", shown(app).strip() in ("", "0"),
          repr(shown(app)))

    # ---- Up/Down walk the history, and are swallowed either way -----------
    press_key(app, "Delete")
    type_it(app, "7*6=")
    first = shown(app)
    press_key(app, "Delete")
    type_it(app, "8*8=")
    check("8*8= gives 64", shown(app).strip() == "64", repr(shown(app)))
    up = press_key(app, "Up")
    check("Up is claimed so it never wanders the keypad focus", up is True)
    # One Up gives the LAST thing typed, as at any command line — not the one
    # before it. (Measured; my first expectation here was wrong, not the code.)
    check("one Up recalls the most recent calculation",
          shown(app).strip() == "8×8", repr(shown(app)))
    press_key(app, "Up")
    check("...and a second Up reaches the one before it",
          shown(app).strip() == "7×6", repr(shown(app)))
    down = press_key(app, "Down")
    check("...and Down too", down is True)
    check("...walking back towards the newest",
          shown(app).strip() == "8×8", repr(shown(app)))

    # ---- Ctrl+C copies the result -----------------------------------------
    press_key(app, "Delete")
    type_it(app, "6*7=")
    copied = press_key(app, "c", ctrl=True)
    check("Ctrl+C is claimed", copied is True)
    clip = Gtk.Clipboard.get_default(Gdk.Display.get_default())
    pump()
    text = clip.wait_for_text()
    check("...and puts the result on the clipboard", text is not None
          and "42" in text, repr(text))

    # ---- a key the calculator does not own must fall THROUGH --------------
    # Esc has to reach nbapp, which is what leaves the app; swallowing it
    # would make the calculator the one app you cannot close with Esc.
    check("Esc falls through so it can still leave",
          press_key(app, "Escape") is False)
    # STO variables made letters calculator vocabulary; fall-through is for
    # Esc, Tab, and genuinely unowned keys, not expression vocabulary.
    press_key(app, "Delete")
    took = press_key(app, "z")
    check("a letter is claimed and enters its uppercase variable",
          took is True and shown(app).strip() == "Z",
          "%r, %r" % (took, shown(app)))
    check("an unowned F5 key falls through", press_key(app, "F5") is False)

    # ---- the on-screen keypad reaches the same place the keyboard does ----
    # _on_press is a one-line wrapper, which is exactly the sort of thing that
    # is assumed to work and then quietly stops being wired to anything.
    press_key(app, "Delete")
    app._on_press(None, ("9", "app", "9", "num"))
    app._on_press(None, ("×", "app", "×", "op"))
    app._on_press(None, ("9", "app", "9", "num"))
    app._on_press(None, ("=", "eq", None, "eq"))
    pump()
    check("a keypad button computes the same as typing", shown(app).strip() == "81",
          repr(shown(app)))

    # ---- Enter on the history box recalls, as clicking it would ------------
    press_key(app, "Delete")
    type_it(app, "5*5=")
    press_key(app, "Delete")
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = Gdk.KEY_Return
    ev.state = Gdk.ModifierType(0)
    ev.string = ""
    ev.window = app.get_window()
    took = app._on_history_key(None, ev)
    pump()
    check("Enter on the history recalls the last calculation",
          took is True and shown(app).strip() == "5×5", repr(shown(app)))
    ev2 = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev2.keyval = Gdk.KEY_Tab
    ev2.state = Gdk.ModifierType(0)
    ev2.string = ""
    ev2.window = app.get_window()
    check("...and Tab there falls through, so focus still moves",
          app._on_history_key(None, ev2) is False)

    # ---- Clear from the menu is the same clear as the key ------------------
    type_it(app, "123")
    app._clear_all()
    pump()
    check("Clear from the menu empties the display",
          shown(app).strip() in ("", "0"), repr(shown(app)))

    # ---- Edit menu offers Copy Result, and it agrees with Ctrl+C ----------
    items = dict((i[0], i[1]) for i in app.menu_items("Edit")
                 if isinstance(i, tuple))
    check("the Edit menu offers Copy Result",
          any("copy" in k.lower() for k in items), repr(sorted(items)))

    try:
        app.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
