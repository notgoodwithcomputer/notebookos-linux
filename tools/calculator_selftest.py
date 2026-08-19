#!/usr/bin/env python3
"""calculator_selftest — check that the calculator is right.

    DISPLAY=:0 python3 tools/calculator_selftest.py

Arithmetic is cheap to test exhaustively and embarrassing to get wrong, so
every expression the keypad can produce is checked against the answer a person
would expect, not against what the implementation happens to do. Two classes in
particular are pinned here because they are the ones that read as "this
calculator is broken":

* **Trig at the cardinal angles.** pi has no exact binary form, so the obvious
  implementation answers sin(180) with 1.22464679915e-16 and tan(90) with
  1.63312393532e+16. Both must be 0 and undefined.
* **Results too long for the display.** An exact 375-digit answer ellipsizes to
  its least significant digits, which tell you nothing; past what one line
  holds the answer is given as a magnitude.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# $CALCULATOR_MODULE_DIR or the repo. Hardcoding the repo path made every red
# proof against this suite VACUOUS: the mutated copy was ignored and the suite
# happily re-measured the pristine file, so a sabotage that should have gone red
# reported "all checks passed". Measured on 2026-08-08 with a mutation that
# makes sanitize_state always answer "degrees".
sys.path.insert(0, os.environ.get("CALCULATOR_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="calc-selftest-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                # noqa: E402
import calculator                                             # noqa: E402

C = calculator.Calculator
FAILS = []


class _Calc(object):
    """The evaluator without the widgets — evaluate() reads self.expr and
    self.deg, and records WHY a failure failed through _fail()."""
    evaluate = C.evaluate
    _fail = C._fail
    # evaluate() answers through _answer(), which remembers the NUMBER
    # beside the text a display mode renders it as. A stub that borrows
    # evaluate must borrow that too, or every case in this file dies with
    # AttributeError before a single check is counted -- which is exactly
    # what it did, and a suite that CRASHES reports no verdict at all.
    _answer = C._answer
    _answer_value = C._answer_value

    def __init__(self, deg=True):
        self.deg = deg
        self.expr = ""
        self._err_why = None


def ev(expr, deg=True):
    c = _Calc(deg)
    c.expr = expr
    return c.evaluate()


def case(expr, want, deg=True):
    got = ev(expr, deg)
    ok = got == want
    if not ok:
        FAILS.append(expr)
    print("%-4s %-24r -> %-22r %s" % ("ok" if ok else "FAIL", expr, got,
                                      "" if ok else "want %r" % want))


# Python function objects expose their module globals through attributes.  A
# calculator expression must never be able to traverse that object graph.
sentinel = os.path.join(os.environ["NB_HOME"], "eval-escaped")
payload = "sin.__globals__['os'].system('touch %s')" % sentinel
case(payload, "Error")
if os.path.exists(sentinel):
    FAILS.append("evaluator sandbox escape")
    print("FAIL evaluator executed a non-arithmetic command")
else:
    print("ok   evaluator rejects attributes, subscripts, and strings")


def check(name, ok, detail=""):
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   " + str(detail)))
    if not ok:
        FAILS.append(name)


print("== order of operations ==")
for e, w in [("2+3×4", "14"), ("(2+3)×4", "20"),
             ("2−3−4", "-5"), ("100÷10÷2", "5"),
             ("2+3×4−6÷3", "12"), ("2^3^2", "512"),
             ("−2^2", "-4"), ("(−2)^2", "4"), ("2×3^2", "18"),
             ("10−2×3", "4"), ("2^-2", "0.25"), ("0^0", "1")]:
    case(e, w)

print("== precision ==")
for e, w in [("0.1+0.2", "0.3"), ("1÷3", "0.333333333333"),
             ("2÷3", "0.666666666667"), ("0.1×3", "0.3"),
             ("1.1+2.2", "3.3"), ("100÷7", "14.2857142857"),
             ("1÷3×3", "1"), ("0.0000001×0.0000001", "1e-14")]:
    case(e, w)

print("== trig at the cardinal angles (degrees) ==")
# the whole point: these are the answers a person expects, and the ones every
# calculator on a desk gives
for e, w in [("sin(0)", "0"), ("sin(30)", "0.5"), ("sin(90)", "1"),
             ("sin(180)", "0"), ("sin(270)", "-1"), ("sin(360)", "0"),
             ("sin(-180)", "0"), ("sin(450)", "1"),
             ("cos(0)", "1"), ("cos(90)", "0"), ("cos(180)", "-1"),
             ("cos(270)", "0"), ("cos(360)", "1"),
             ("tan(0)", "0"), ("tan(45)", "1"), ("tan(135)", "-1"),
             ("tan(180)", "0"), ("tan(360)", "0"),
             # tan is undefined at 90 and 270 - saying so beats inventing 1.6e16
             ("tan(90)", "Error"), ("tan(270)", "Error"), ("tan(-90)", "Error")]:
    case(e, w)

print("== trig off the cardinal angles is untouched ==")
for e, w in [("sin(45)", "0.707106781187"), ("cos(30)", "0.866025403784"),
             ("tan(60)", "1.73205080757"), ("sin(89.9)", "0.999998476913")]:
    case(e, w)

print("== radians ==")
for e, w in [("sin(0)", "0"), ("cos(0)", "1"), ("tan(0)", "0")]:
    case(e, w, deg=False)

print("== inverse trig (degrees) ==")
for e, w in [("asin(1)", "90"), ("asin(0.5)", "30"), ("acos(0)", "90"),
             ("acos(1)", "0"), ("atan(1)", "45"), ("asin(2)", "Error")]:
    case(e, w)

print("== roots, powers, logs ==")
for e, w in [("√(9)", "3"), ("√(2)", "1.41421356237"),
             ("2^0.5", "1.41421356237"), ("log(100)", "2"), ("ln(e)", "1"),
             ("log(1000)", "3"), ("2^10", "1024"), ("√(0)", "0")]:
    case(e, w)

print("== factorial ==")
for e, w in [("5!", "120"), ("0!", "1"), ("1!", "1"), ("10!", "3628800"),
             # the x! key is labelled Factorial; it has to work on whatever the
             # keypad can put in front of it, not only on a run of digits
             ("(2+3)!", "120"), ("√(9)!", "6"), ("(2×3)!", "720"),
             ("3×(2+1)!", "18"),
             # ...and refuse what factorial is not defined on, rather than
             # rounding it and answering a different question
             ("5.5!", "Error"), ("(-1)!", "Error"), ("!", "Error"),
             ("!5", "Error")]:
    case(e, w)

print("== constants and implicit multiplication ==")
for e, w in [("π", "3.14159265359"), ("2π", "6.28318530718"),
             ("e", "2.71828182846"), ("2(3)", "6"), ("(1+1)(2)", "4"),
             ("3sin(0)", "0"), ("2e", "5.43656365692"),
             # a previous result in scientific notation must parse back
             ("1e-05", "1e-05"), ("1e-05×2", "2e-05"), ("2e3", "2000")]:
    case(e, w)

print("== unclosed parentheses are forgiven ==")
for e, w in [("√(9", "3"), ("log(100", "2"), ("(2+3", "5"),
             ("sin(30", "0.5"), ("((1+2", "3")]:
    case(e, w)

print("== errors are errors, never a wrong number ==")
for e, w in [("1÷0", "Error"), ("√(-1)", "Error"),
             ("log(0)", "Error"), ("ln(-1)", "Error"), ("+", "Error"),
             ("2++", "Error"), (")(", "Error"), ("", "0"),
             ("9^9^9", "Error"), ("1e308×10", "Error"),
             ("100000!", "Error")]:
    case(e, w)

print("== long results are readable, not ellipsised zeros ==")
case("10^400", "1e+400")
case("200!", "7.88657867365e+374")
case("2^200", "1.60693804426e+60")
# ...but an answer that DOES fit stays exact to the digit
case("20!", "2432902008176640000")
case("2^62", "4611686018427387904")
check("_sci rounds, it does not truncate",
      calculator._sci("788657867364790503") == "7.88657867365e+17",
      calculator._sci("788657867364790503"))
check("_sci carries a rounding into the exponent",
      calculator._sci("99999999999999") == "1e+14",
      calculator._sci("99999999999999"))
check("_sci keeps a negative", calculator._sci("-12345678901234567890")
      == "-1.23456789012e+19", calculator._sci("-12345678901234567890"))

print("== display and history ==")
if not Gtk.init_check()[0]:
    print("SKIP GTK display/history checks: no display connection")
    print("\n%d check(s) failed" % len(FAILS) if FAILS else "\nall headless checks passed")
    sys.exit(len(FAILS))
app = C()
app.press(("1", "app", "1", "num"))
app.press(("+", "app", "+", "op"))
app.press(("2", "app", "2", "num"))
app.press(("=", "eq", None, "eq"))
check("= computes and shows the result", app.disp_lbl.get_text() == "3",
      app.disp_lbl.get_text())
check("the calculation is kept", app.tape == ["1+2"], app.tape)
check("Up brings it back", app.recall(-1) and app.expr == "1+2", app.expr)
check("Down past the newest restores the display",
      app.recall(1) and app.expr == "3", app.expr)
app.press(("=", "eq", None, "eq"))
app.press(("9", "app", "9", "num"))
app.press(("=", "eq", None, "eq"))
check("history is oldest-first", app.tape == ["1+2", "3", "9"], app.tape)
check("Up walks back one at a time",
      [app.recall(-1) and app.expr for _ in range(3)] == ["9", "3", "1+2"],
      app.tape)
check("Up at the oldest stays there", app.recall(-1) and app.expr == "1+2",
      app.expr)
app.press(("AC", "ac", None, "clear"))
check("AC clears the display but keeps the history",
      app.expr == "" and app.tape == ["1+2", "3", "9"], (app.expr, app.tape))
for i in range(60):
    app._remember("%d+1" % i)
check("history is capped", len(app.tape) == app._TAPE_MAX, len(app.tape))
check("the cap drops the OLDEST", app.tape[-1] == "59+1", app.tape[-1])
app._remember("59+1")
check("a repeat is not stored twice", len(app.tape) == app._TAPE_MAX,
      len(app.tape))

# Every failure names its own cause. "Error" told a person nothing about which
# mistake they had made; these four sentences are the only things the display
# can ever say when "=" cannot be answered, so each cause is pinned to one.
for _expr, _why, _label in (
        ("1÷0", calculator._WHY_ZERO, "divide by zero"),
        ("9^9^9", calculator._WHY_TOOBIG, "too big"),
        ("√(0−1)", calculator._WHY_NOANSWER, "no answer"),
        ("2+×", calculator._WHY_UNREADABLE, "not a calculation")):
    _c = _Calc(True)
    _c.expr = _expr
    _got = _c.evaluate()
    check("%-18s -> %s" % (_expr, _label),
          _got == "Error" and _c._err_why == _why, (_got, _c._err_why))

# a failed calculation is still worth getting back to fix
app2 = C()
app2.expr = "1÷0"
app2.press(("=", "eq", None, "eq"))
# The display says WHAT went wrong, not the word "Error" — that is the whole
# point of the change, so pin it here: a divide by zero must name itself.
check("a failed calculation says what went wrong",
      app2.disp_lbl.get_text() == calculator._WHY_ZERO,
      app2.disp_lbl.get_text())
check("...and the display is in the alert style",
      app2.disp_lbl.get_style_context().has_class("err"))
check("...and can still be recalled to fix",
      app2.recall(-1) and app2.expr == "1÷0", app2.expr)

# angle mode survives a relaunch, and only that.
#
# These two checks used to call `_load_prefs()`, a second reader of
# calculator.json that NOTHING IN THE APP CALLED -- __init__ has taken the angle
# mode from _load_state/sanitize_state since the tape was added. So the suite
# was asserting that a method the app never runs could read the file, and a
# relaunch that genuinely lost the setting would not have failed either line.
# The method is gone; these now open the app the way a person does and read the
# mode off the instance.
app3 = C()
app3._set_deg(False)
check("radians is remembered", C().deg is False)
app3._set_deg(True)
check("degrees is remembered", C().deg is True)
check("the calculator still opens empty", C().expr == "")

# ------------------------------------------------------- the 2nd modifier
# The mutation sweep survived a swap in `self.second and value in ALT_VALUE`,
# and the triage confirmed it changes behaviour: with `or`, pressing sin WITHOUT
# 2nd inserts asin(. A calculator that silently takes the arc- function when the
# person asked for the plain one is worse than one that errors.
_K = {k[0]: k for k in calculator.KEYS}


def _keys(names):
    _c = C()
    _c.expr = ""
    _c.second = False
    for _n in names:
        _c.press(_K[_n])
    return _c.expr


check("2nd then sin gives the inverse", _keys(["2nd", "sin"]) == "asin(",
      _keys(["2nd", "sin"]))
check("sin on its own does NOT", _keys(["sin"]) == "sin(", _keys(["sin"]))
check("2nd pressed twice cancels itself",
      _keys(["2nd", "2nd", "sin"]) == "sin(", _keys(["2nd", "2nd", "sin"]))
check("2nd then a key with no inverse just types that key",
      _keys(["2nd", "7"]) == "7", _keys(["2nd", "7"]))

# ----------------------------------------- the display follows the mode
# The mutation sweep survived swaps in `_refresh`'s and `_sync_dynamic_keys`'s
# cache guards -- `if mode != self._mode_txt` and
# `if self._face_cache.get(lbl) == key`. Those caches exist so typing a digit
# does not force a keypad relayout, and the failure they can produce is a
# display that quietly stops following the state: the mode still changes, the
# LABEL still says DEGREES. I had checked this by hand and recorded it as
# correct, which is not the same as pinning it.
_m = C()
_m._set_deg(True)
_deg_text = _m.mode_lbl.get_text()
_m._set_deg(False)
_rad_text = _m.mode_lbl.get_text()
check("the mode line says which mode it is in",
      _deg_text != _rad_text and _deg_text and _rad_text,
      (_deg_text, _rad_text))
_m._set_deg(True)
check("...and goes back when the mode does",
      _m.mode_lbl.get_text() == _deg_text, _m.mode_lbl.get_text())

_plain = [f.get_text() for _l, _b, f in _m._inv_btns]
_m.second = True
_m._sync_dynamic_keys()
_inv = [f.get_text() for _l, _b, f in _m._inv_btns]
_m.second = False
_m._sync_dynamic_keys()
_back = [f.get_text() for _l, _b, f in _m._inv_btns]
check("the sin/cos/tan keys show their inverse face while 2nd is on",
      _plain != _inv and len(_inv) == 3, (_plain, _inv))
check("...and show it no longer when 2nd goes off", _back == _plain,
      (_back, _plain))

# ------------------------------------------------- the +/- key
# The arithmetic sweep survived a swap in `("−(" + self.expr + ")")`, which is
# string concatenation -- with a minus it raises TypeError. It survived because
# NOTHING pressed +/- on a non-empty expression. On an empty display the key
# takes a different branch, which is the one every existing check happened to
# exercise.
check("+/- on an empty display starts a negative number",
      _keys(["±"]) == "−", _keys(["±"]))
check("+/- wraps what is already typed", _keys(["5", "±"]) == "−(5)",
      _keys(["5", "±"]))
check("...including a whole expression",
      _keys(["1", "+", "2", "±"]) == "−(1+2)", _keys(["1", "+", "2", "±"]))

# ------------------------------------------------------- Copy Result
# The display holds a translated SENTENCE when a calculation fails ("Cannot
# divide by zero"), and pasting that into a spreadsheet would be worse than
# pasting nothing -- it silently replaces whatever useful value the clipboard
# already held with UI prose. Copy Result declines on an error, which is a
# deliberate decision and exactly the kind a later tidy-up removes.
_sent = []


class _ClipSpy(object):
    def set_text(self, text, _len):
        _sent.append(text)

    def store(self):
        pass


_real_clip = Gtk.Clipboard.get
Gtk.Clipboard.get = staticmethod(lambda _sel: _ClipSpy())
try:
    _c = C()
    _c.expr = "1+2"
    _c.error = False
    _c.press(("=", "eq", None, "eq"))
    _c._copy_result()
    check("Copy Result puts the answer on the clipboard",
          _sent == ["3"], _sent)
    _n = len(_sent)
    _c.expr = "1÷0"
    _c.error = False
    _c.press(("=", "eq", None, "eq"))
    check("...a failed calculation shows why, in words",
          _c.disp_lbl.get_text() == calculator._WHY_ZERO, _c.disp_lbl.get_text())
    _c._copy_result()
    check("...and Copy Result declines to paste that sentence anywhere",
          len(_sent) == _n, _sent[_n:])
finally:
    Gtk.Clipboard.get = _real_clip

# ---------------------------------------------------------------- _sci
# A 375-digit exact answer is not readable on one line: the display ellipsizes
# it and shows only its LAST digits, which say nothing about how big it is.
# _sci rounds the mantissa out of the DIGIT STRING, because float() overflows
# past ~1e308 and cannot help. That is hand-rolled decimal arithmetic, so it is
# graded against the real thing.
#
# Graded on RELATIVE ERROR, not on the exponent. My first version of this
# compared exponents and reported seven failures on the all-nines cases -- and
# every one of them was the check being wrong: 21 nines is 10^21 - 1, and
# rounding that to 12 significant figures carries legitimately into 1e+21. The
# exponent is SUPPOSED to move there.
import random as _random                                      # noqa: E402
from decimal import Decimal as _D, getcontext as _ctx         # noqa: E402
_ctx().prec = 80
_random.seed(3)
_cases = []
for _n in (21, 22, 23, 30, 50, 100, 375):
    _cases += ["9" * _n, "1" + "0" * (_n - 1), "9" * (_n - 1) + "5",
               "1" + "9" * (_n - 1),
               ("".join(_random.choice("0123456789")
                        for _ in range(_n)).lstrip("0") or "1")]
_cases += ["-" + "9" * 40, "-" + "1234567890" * 4, "5" + "0" * 30,
           "999999999999" + "0" * 20]
_bad = []
for _s in _cases:
    _out = calculator._sci(_s)
    _got, _want = _D(_out), _D(_s)
    _mant = abs(_got.scaleb(-_got.adjusted()))
    if not (1 <= _mant < 10):
        _bad.append((_s[:14], _out, "mantissa %s" % _mant))
    elif abs(_got - _want) / abs(_want) > _D("5e-12"):
        _bad.append((_s[:14], _out, "relative error"))
    elif len(str(_mant).replace(".", "").rstrip("0")) > 12:
        _bad.append((_s[:14], _out, "more than 12 significant figures"))
check("a very long exact answer is shown to 12 significant figures, correctly "
      "rounded (%d cases)" % len(_cases), not _bad, _bad[:3])

print("\n%d check(s) failed" % len(FAILS) if FAILS else "\nall checks passed")
sys.exit(len(FAILS))
