#!/usr/bin/env python3
"""STO-> stores what is on the display, including the things it could not.

    tools/guestrun.sh python3 tools/calculator_variables_selftest.py

`_store_dialog` read the value as `float(self.expr)` behind an
`except ValueError: pass`. So it worked when the display already held a bare
decimal, and every other case did nothing AND SAID NOTHING — the dialog closed,
no variable appeared, no message was shown, and whatever the variable held
before was still there. Measured on the module as it stood:

    3          stored 3.0
    1+2        stored NOTHING      any unevaluated expression
    sqrt(9)    stored NOTHING      anything with a function
    2*PI       stored NOTHING      anything with a constant
    -5         stored NOTHING      <- the keypad's OWN minus key

That last row is what makes this a defect rather than a limitation. The minus
key inserts U+2212 MINUS SIGN, not ASCII hyphen, and `float()` does not accept
it — so the calculator could not store a negative number typed on its own
keypad, and said nothing about it.

The value now goes through the app's own evaluator, which is what "=" uses and
what every one of those rows needed. An expression that genuinely cannot be read
still returns None, and evaluate() has already put the reason on the display by
the time it does.

WHY THE HELPER EXISTS SEPARATELY FROM THE DIALOG: the read sat inside
`_store_dialog`, behind a `dialog.run()` that no test can drive without a modal
loop. It was therefore unreachable by every suite this app has, which is why a
feature that silently failed on most of its inputs shipped. Lifting the one
decision out — the same move `window_is_valid` needed — is what makes it
checkable at all.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALCULATOR_MODULE_DIR. MEASURED — one of the three lands and the other
two are equivalent, which is recorded here rather than dressed up:

  1. the value is read straight off the display again, as it shipped
     (`result = self.evaluate()` -> `result = self.expr`)          8 FAILED
       FAIL 1+2 stores 3
       FAIL sqrt(9) stores 3
       FAIL 2*PI stores 6.28318530718
       FAIL an empty display stores 0
       FAIL the keypad's own minus sign stores a negative number
       FAIL ...and so does a subtraction written with it
       FAIL a stored negative survives being saved and read back
       FAIL ...and so does a computed constant
     Every one of those was silent in the shipped app: the dialog closed, the
     variable kept its old value, nothing was said.

  2. a non-finite result is stored rather than refused
     (`return value if math.isfinite(value) else None` -> `return value`)
                                                                   0 FAILED
     EQUIVALENT, measured. evaluate() already refuses an overflow through
     _WHY_TOOBIG, so no expression this app can parse reaches that line. It
     guards a future evaluator, not a reachable state, and a check written to
     make it look covered would be a check that cannot fail.

  4. the variable list goes back to str(float)
     (`format_number(value, self.fix)` -> the raw tuple format)     2 FAILED
       FAIL the variable list uses the app's number format, not str(float)
       FAIL ...including a constant, at the display's precision
     What it listed: "A = 1.0" where the display says "1", and
     "B = 0.30000000000000004" where the display says "0.3" — the float noise
     %.12g exists to hide, shown only in this one dialog.

  5. the Window dialog labels its rows with the dict keys again
     (`key.capitalize()` -> `key`)                                  1 FAILED
       FAIL the Window dialog labels its rows the way a graphing calculator does
     It read `xmin`, `xmax`, `yscl` — programmer identifiers, in the one place
     a graphing calculator has always said Xmin/Xmax/Yscl.

  6. the Window dialog's bounds go back to str(float)
     (`format_number(...)` -> `str(...)`)                           1 FAILED
       FAIL ...and its bounds are in the app's number format too
       ("[-10.0]" where the rest of the app writes "-10")

  3. the explicit "Error" test is removed
     (`if result == "Error": return None` -> never taken)          0 FAILED
     EQUIVALENT, measured, and for a good reason: `float("Error")` raises
     ValueError, which the except clause below already turns into None. The
     line is belt-and-braces and says the intent out loud; it is not what makes
     the behaviour correct. Kept for the reader, not counted as covered.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-vars-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
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


app = calculator.Calculator()
for _ in range(6):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def stored(expression):
    app.expr = expression
    app.error = False
    return app._store_value()


# ------------------------------------------------------------ what must store
STORES = [
    ("3", 3.0, "a bare number, the only case that ever worked"),
    ("1+2", 3.0, "1+2 stores 3"),
    ("sqrt(9)", 3.0, "sqrt(9) stores 3"),
    ("2*PI", 6.28318530718, "2*PI stores 6.28318530718"),
    ("1.5e3", 1500.0, "scientific notation stores 1500"),
    ("", 0.0, "an empty display stores 0"),
]
for expression, want, name in STORES:
    got = stored(expression)
    check(name, got is not None and abs(got - want) < 1e-9,
          "stored %r, wanted %r" % (got, want))

# The one that makes this a bug rather than a limitation.
check("the keypad's own minus sign stores a negative number",
      stored("−5") == -5.0, stored("−5"))
check("...and so does a subtraction written with it",
      stored("3−8") == -5.0, stored("3−8"))

# ------------------------------------------------------- what must NOT store
for expression, name in (("((", "an unreadable expression stores nothing"),
                         ("1/0", "a division by zero stores nothing"),
                         ("9^9^9^9", "an oversized power stores nothing")):
    check(name, stored(expression) is None, stored(expression))

# --------------------------------------------- and the value survives a save
# A stored variable is only useful if it is still there next time, and
# sanitize_state screens variables to single uppercase letters holding finite
# numbers -- so a value that stores fine and does not round-trip is no better
# than one that never stored.
app.variables["A"] = stored("3−8")
app.variables["B"] = stored("2*PI")
app._save_prefs()
back = calculator.sanitize_state(app._load_state())["variables"]
check("a stored negative survives being saved and read back",
      back.get("A") == -5.0, back)
check("...and so does a computed constant",
      back.get("B") is not None and abs(back["B"] - 6.28318530718) < 1e-9, back)

# ------------------------------------------- what the DIALOGS put on screen
# Every dialog in this app builds and `run()`s in one method, so none of them
# has ever been reachable by a test -- which is how a feature that silently
# failed on most of its inputs shipped, and how these two formatting faults
# shipped beside it. Stubbing run() to return CANCEL lets the dialog be built
# and read without a modal loop. It is a real Gtk.Dialog either way.
_real_run = Gtk.Dialog.run
_seen = {}


def _capture_run(self):
    self.show_all()
    for _ in range(20):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    texts = []

    def walk(w):
        if isinstance(w, Gtk.Label) and w.get_text().strip():
            texts.append(w.get_text().strip())
        if isinstance(w, Gtk.Entry):
            texts.append("[" + w.get_text() + "]")
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    walk(self)
    _seen[self.get_title()] = texts
    return Gtk.ResponseType.CANCEL


Gtk.Dialog.run = _capture_run
try:
    app.variables = {"A": 1.0, "B": 0.1 + 0.2, "C": 2 * 3.141592653589793}
    app._variables_dialog()
    app._window_dialog()
finally:
    Gtk.Dialog.run = _real_run

listing = " ".join(_seen.get("Variables", []))
# str(float) listed variables in a DIFFERENT number format from every other
# surface in the app: a stored 1 read "1.0", and 0.1+0.2 listed as
# "0.30000000000000004" while the display showed "0.3".
check("the variable list uses the app's number format, not str(float)",
      "A = 1" in listing and "B = 0.3" in listing
      and "0.30000000000000004" not in listing, listing)
check("...including a constant, at the display's precision",
      "C = 6.28318530718" in listing, listing)

window = " ".join(_seen.get("Window", []))
check("the Window dialog labels its rows the way a graphing calculator does",
      all(k in window for k in ("Xmin", "Xmax", "Ymin", "Ymax", "Xscl", "Yscl")),
      window)
check("...and its bounds are in the app's number format too",
      "[-10]" in window and "[-10.0]" not in window, window)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
