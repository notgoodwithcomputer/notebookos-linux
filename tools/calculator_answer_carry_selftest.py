#!/usr/bin/env python3
"""What carries on from an answer is the ANSWER, and a paste survives the next key.

    tools/guestrun.sh python3 tools/calculator_answer_carry_selftest.py

FOUR defects, all on the path between "=" and whatever the person does next,
all measured by pressing the keys.

1 and 2 -- A DISPLAY SETTING BECAME THE ARITHMETIC, on the two keys that were
missed when this was fixed for the operator keys. Fix mode says how many
decimals to SHOW. `press()` builds the next expression by wrapping what is on
the display, and after "=" the display holds a rounding, not the answer:

        Fix 2   2÷3 =    0.67     then 1/x =    1.49   (the answer is 1.5)
        Fix 2   2÷3 =    0.67     then +/- =   -0.67, and Ans became -0.67,
                                  so ×3 = came back -2.01 (the answer is -2)

    The operator keys already had the cure -- `_continued_answer()`, which
    hands back the display text when the display IS the answer and the token
    "Ans" when it is not. +/- and 1/x did not use it. This is the same finding
    the `_answer` docstring records for ×, and it is written there as fixed.

3 -- STO-> STORED THE ROUNDING. `_store_value` evaluates the DISPLAY, and
straight after "=" the display is that same rounding:

        Fix 0   1250÷12 =   104   STO-> B stored 104.0
                                  (the answer is 104.166666667)

    The `_answer` docstring names this exact case, to the digit, as one of the
    four it fixed. It was not fixed: the number was kept beside the text, and
    this caller went on reading the text.

4 -- A PASTE WAS THROWN AWAY BY THE NEXT KEY. `_paste_expression` replaced the
expression and left `just_evaled` set, so the display was still marked "this
is the last answer, carry on from it":

        7×7 =   49    Ctrl+V "12"   12    then 5  ->  5      (should be 125)
        7×7 =   49    Ctrl+V "12"   12    then +  ->  Ans+   (should be 12+)

    In the second one the pasted number is not merely lost, it is replaced by
    the answer it was pasted over -- the calculator then works out a sum the
    person never typed.

RED PROOFS (M1..M5), each applied ALONE to a scratch copy, this suite pointed
at it with CALCULATOR_MODULE_DIR. All five MEASURED:

  1. +/- and 1/x wrap the display again
     (`inner = self._continued_answer() if ... else self.expr`
      -> `inner = self.expr`)                                    3 FAILED
       FAIL Fix 2: 1/x on 2÷3 answers 1.50
            <- shown '0.67' then '1.49'
       FAIL Fix 2: +/- on 2÷3 leaves Ans the answer, so ×3 is -2.00
            <- shown '0.67', negated '-0.67', then '-2.01'
       FAIL Fix 0: +/- on 1250÷12 leaves Ans the answer, so ×12 is -1250
            <- shown '104', negated '-104', then '-1248'
     Three, not four: Fix 0's 1/x of 104 and of 104.166666667 both round to
     "0", so that half of the pair cannot tell the two apart. The pair is
     still written out, because the Fix 2 half of it can.

  2. _store_value evaluates the display again
     (the `if self.just_evaled:` block removed)                  3 FAILED
       FAIL Fix 0: STO-> stores the answer, not the 104 shown   <- 104.0
       FAIL Fix 2: STO-> stores the answer, not the 0.67 shown  <- 0.67
       FAIL ...and a variable stored under Fix reads back the same in Float
            <- 0.67

  3. the paste leaves just_evaled set
     (`self.just_evaled = False` removed from `received`)        3 FAILED
       FAIL a digit after a paste extends it              <- 5
       FAIL an operator after a paste continues it        <- Ans+
       FAIL ...and works out what was pasted              <- 52

  4. the paste leaves the tape walk open
     (`self._tape_i = None` removed from `received`)             1 FAILED
       FAIL Up after a paste recalls the newest calculation  <- 1+1

  5. the pasted spelling is left as the clipboard had it
     (`replace("*", "×")` flipped back)                          1 FAILED
       FAIL a pasted expression is spelled the way the keypad spells it
            <- 2*3+4/2

The Float-mode pairs are here on purpose. Under Float the display text IS the
answer, so every one of these keys is already right -- and a "fix" that
replaced the display with "Ans" unconditionally would pass the Fix checks and
lose the exactness of a 20-digit integer. Each defect is therefore checked as
a PAIR: wrong under Fix, unchanged under Float.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
# SET, not setdefault: guestrun.sh exports NB_HOME, and this suite reads back
# what it stored.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="calc-carry-")
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


AC = ("AC", "ac", None, "clear")
EQ = ("=", "eq", None, "eq")
NEG = ("±", "neg", None, "fn")
INV = ("1/x", "inv", None, "fn")

app = calculator.Calculator()


def worked_out(expr, fix):
    """Type `expr` and press "=", under display mode `fix`."""
    app.fix = fix
    app.press(AC)
    app.expr = expr
    app.press(EQ)
    return app.disp_lbl.get_text()


def then(*keys):
    for kd in keys:
        app.press(kd)
    return app.disp_lbl.get_text()


# ------------------------------------------------------------------- 1/x
for fix, expr, shown, want in ((2, "2÷3", "0.67", "1.50"),
                               (0, "1250÷12", "104", "0")):
    got_shown = worked_out(expr, fix)
    got = then(INV, EQ)
    check("Fix %d: 1/x on %s answers %s" % (fix, expr, want),
          got_shown == shown and got == want, "shown %r then %r" % (got_shown, got))

# Float is the mode where the display IS the answer: the same keys must still
# wrap the display itself, digits and all.
worked_out("2÷3", None)
check("Float: 1/x wraps the answer on the display",
      then(INV) == "1÷(0.666666666667)", app.expr)
check("...and works out to 1.5", then(EQ) == "1.5", app.disp_lbl.get_text())

# An exact 20-digit integer must not be turned into a rounded "Ans" round trip.
worked_out("99999999999999999999", None)
check("Float: 1/x on an exact 20-digit answer keeps the digits",
      then(INV) == "1÷(99999999999999999999)", app.expr)

# ------------------------------------------------------------------- +/-
MUL = ("×", "app", "×", "op")
DIGIT = lambda ch: (ch, "app", ch, "num")
for fix, expr, shown, mult, want in ((2, "2÷3", "0.67", "3", "-2.00"),
                                     (0, "1250÷12", "104", "12", "-1250")):
    got_shown = worked_out(expr, fix)
    negated = then(NEG, EQ)
    got = then(MUL, *[DIGIT(c) for c in mult], EQ)
    check("Fix %d: +/- on %s leaves Ans the answer, so ×%s is %s"
          % (fix, expr, mult, want),
          got_shown == shown and got == want,
          "shown %r, negated %r, then %r" % (got_shown, negated, got))

worked_out("5", None)
check("Float: +/- on 5 is -5", then(NEG, EQ) == "-5", app.disp_lbl.get_text())
app.press(AC)
check("+/- on an empty display still starts a negative number",
      then(NEG) == "−", app.expr)

# ------------------------------------------------------------------- STO->
worked_out("1250÷12", 0)
app.store_from_display("B")
check("Fix 0: STO-> stores the answer, not the 104 shown",
      abs(app.variables.get("B", 0) - 1250 / 12) < 1e-9, app.variables.get("B"))
worked_out("2÷3", 2)
app.store_from_display("A")
check("Fix 2: STO-> stores the answer, not the 0.67 shown",
      abs(app.variables.get("A", 0) - 2 / 3) < 1e-9, app.variables.get("A"))
app.fix = None
app.press(AC)
app.expr = "A"
app.press(EQ)
check("...and a variable stored under Fix reads back the same in Float",
      app.disp_lbl.get_text() == "0.666666666667", app.disp_lbl.get_text())

# An expression the person is still part-way through is still EVALUATED, which
# is what _store_value was written for -- the just_evaled shortcut must not
# swallow that case.
app.fix = None
app.press(AC)
app.expr = "sqrt(9)"
app.store_from_display("C")
check("STO-> still evaluates an unfinished expression",
      app.variables.get("C") == 3.0, app.variables.get("C"))
app.press(AC)
app.expr = "−5"
app.store_from_display("D")
check("...including the keypad's own minus sign",
      app.variables.get("D") == -5.0, app.variables.get("D"))

# ------------------------------------------------------------------- paste
def paste(text):
    """What the clipboard callback does, with the app's own code."""
    app._clipboard_received = True
    value = app._clipboard_expression(text)
    assert value is not None, text
    # Drive the real handler by handing the app a clipboard that answers at once.
    class _Clip:
        def request_text(self, cb):
            cb(self, text)
        def set_text(self, *_a):
            pass
        def store(self):
            pass
    real = Gtk.Clipboard.get
    Gtk.Clipboard.get = staticmethod(lambda _sel: _Clip())
    try:
        app._paste_expression()
    finally:
        Gtk.Clipboard.get = real
    return app.disp_lbl.get_text()


app.fix = None
worked_out("7×7", None)
check("a paste lands on the display", paste("12") == "12", app.disp_lbl.get_text())
check("a digit after a paste extends it",
      then(("5", "app", "5", "num")) == "125", app.disp_lbl.get_text())
worked_out("7×7", None)
paste("12")
check("an operator after a paste continues it",
      then(("+", "app", "+", "op")) == "12+", app.disp_lbl.get_text())
check("...and works out what was pasted",
      then(("3", "app", "3", "num"), EQ) == "15", app.disp_lbl.get_text())
check("a pasted expression is spelled the way the keypad spells it",
      (app.press(AC), paste("2*3+4/2"))[1] == "2×3+4÷2", app.expr)
check("...and works out the same", then(EQ) == "8", app.disp_lbl.get_text())

# A paste ends a walk back through the tape, the way typing does.
app.press(AC)
for e in ("1+1", "2+2", "3+3"):
    app.expr = e
    app.press(EQ)
app.press(AC)
app.recall(-1)
app.recall(-1)                                  # part-way back through the tape
paste("50")
app.recall(-1)
check("Up after a paste recalls the newest calculation",
      app.expr == "3+3", app.expr)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
