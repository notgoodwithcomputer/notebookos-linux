#!/usr/bin/env python3
"""A long sum must not push the keypad off the screen.

    tools/guestrun.sh python3 tools/calculator_tape_fit_selftest.py

WHAT A PERSON DOES: adds up a receipt. Forty-two items at 12.99 is 250
characters, one under the 256 this calculator's own expression bound allows,
and it is an entirely ordinary thing to type into a calculator.

WHAT HAPPENED, driven at 1024x740 and looked at:

    card 640 -> 1690 px wide, on a 1024 px screen

Everything past the fifth keypad column was off the right-hand edge with no
way to reach it -- AC, backspace, the closing bracket, and every operator key
including "=". The mode line said nothing because DEGREES was off the edge
too. And it did not come back: AC clears the expression, not the tape, so the
calculator stayed that shape for the rest of the session and every session
after it, because the tape is saved.

THE CAUSE is a natural width nobody bounded. A tape row was a plain
Gtk.Label, and a plain label asks for the whole string as its natural size.
The tape sits in a ScrolledWindow whose horizontal policy is NEVER -- that
policy does not clamp the request, it just refuses to scroll -- so the row's
width went straight into the card, and the card is what the keypad is inside.

The two display labels directly above the tape had already been given the
cure, and the comment on them says why in as many words: "without that cap an
ellipsizing label still asks for the whole string as its natural size, so a
long expression widened the card". The tape rows, added later, got neither
half of it.

RED PROOF (M1), the two lines removed from `_tape_label` on a scratch copy
(`set_ellipsize` / `set_max_width_chars` for the expression), the suite
pointed at the copy with CALCULATOR_MODULE_DIR:

    FAIL a 250-character sum leaves the card its designed width
         <- card wants 1690px, the screen is 1024
    FAIL ...and the keypad is still reachable across that width
         <- keypad wants 1690px inside a 640px card
    2 checks failed

Both of those are the same fact seen from the two places it hurts, and they
are written apart on purpose: the first is the card, which is what a screenshot
shows; the second is the keypad, which is what a finger cannot reach.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
# SET, not setdefault: guestrun.sh exports NB_HOME, so a setdefault here leaves
# the suite reading whatever the last run of any app left in the shared home --
# and this one measures a widget built from the stored tape.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="calc-tapefit-")
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402

uishot.load_theme()
SCREEN = (1024, 740)
nbapp.screen_size = lambda: SCREEN
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


def by_class(root, name):
    out, seen, i = [], [root], 0
    while i < len(seen):
        w = seen[i]; i += 1
        if name in w.get_style_context().list_classes():
            out.append(w)
        if isinstance(w, Gtk.Container):
            seen.extend(w.get_children())
    return out


app = calculator.Calculator()
child = app.get_child()
# A widget that is not visible reports a preferred width of zero, and a check
# that compares zero against a cap is green for the wrong reason. Show the tree
# (without mapping a toplevel onto the developer's screen) so every measurement
# below is a real one.
child.show_all()
pump()
card = by_class(child, "calccard")[0]
keypad = by_class(card, "keypad")[0]

# The width the card is DESIGNED to be: 640, or the screen less the desk
# margin on a narrower one. _keypad computes the same number.
DESIGNED = max(320, min(640, SCREEN[0] - 96))

empty_card = card.get_preferred_width()[1]
check("an empty calculator is its designed width",
      empty_card == DESIGNED, "card wants %spx, designed %s" % (empty_card, DESIGNED))

# --------------------------------------------------------------- the receipt
LONG = "+".join(["12.99"] * 42)[:250]
assert len(LONG) == 250 and len(LONG) <= calculator.MAX_EXPRESSION_CHARS
app.expr = LONG
app.press(("=", "eq", None, "eq"))
pump()

wide = card.get_preferred_width()[1]
check("a 250-character sum leaves the card its designed width",
      wide <= DESIGNED,
      "card wants %spx, the screen is %s" % (wide, SCREEN[0]))
# The keypad's OWN request never changes -- it is the card around it that grows,
# and the card is what carries the keys off the edge. So measure the desk the
# card sits on: what the home page as a whole asks the screen for.
stage = by_class(child, "calcstage")[0]
desk = stage.get_preferred_width()[1]
check("...and the whole page still fits the screen",
      desk <= SCREEN[0],
      "the home page wants %spx of a %spx screen; the keypad's own request is "
      "still %spx, so what goes off the edge is the card around it"
      % (desk, SCREEN[0], keypad.get_preferred_width()[1]))

# The bound must not have been bought by throwing the row away.
rows = app.tape_box.get_children()
labels = [w for row in rows for w in row.get_children() if isinstance(w, Gtk.Label)]
check("...and the row still says which calculation it was",
      any(l.get_text() == LONG for l in labels),
      [l.get_text()[:20] for l in labels])
TOTAL = calculator.format_number(sum(float(t) for t in LONG.split("+")))
check("...and still shows what it came to",
      any(l.get_text() == TOTAL for l in labels),
      (TOTAL, [l.get_text() for l in labels]))

# A result is never ellipsized away: the longest one this calculator prints is
# a 20-digit exact integer with its sign, and that must fit the cap whole.
app.press(("AC", "ac", None, "clear"))
app.expr = "99999999999999999999"          # 20 digits, exact
app.press(("=", "eq", None, "eq"))
pump()
rows = app.tape_box.get_children()
result_lbl = [w for w in rows[-1].get_children() if isinstance(w, Gtk.Label)][-1]
check("the longest answer still fits its column whole",
      result_lbl.get_max_width_chars() >= len(result_lbl.get_text()),
      "cap %s chars, answer %s chars"
      % (result_lbl.get_max_width_chars(), len(result_lbl.get_text())))
check("...and the card is still its designed width",
      card.get_preferred_width()[1] <= DESIGNED, card.get_preferred_width())

# ------------------------------------------------- and a store from elsewhere
# The tape is read off the disk, so its bound cannot live only in the widget.
state = calculator.sanitize_state({"tape": ["9" * 5000],
                                   "tape_results": ["8" * 5000]})
check("a stored tape row is bounded on the way in",
      len(state["tape"][0]) <= calculator.MAX_EXPRESSION_CHARS
      and len(state["tape_results"][0]) <= calculator.MAX_EXPRESSION_CHARS,
      (len(state["tape"][0]), len(state["tape_results"][0])))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
