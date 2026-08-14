#!/usr/bin/env python3
"""In Yiddish the calculator is still a calculator.

    NB_LANG=yi tools/guestrun.sh python3 tools/calculator_rtl_selftest.py

`nbapp` sets the process-wide GTK direction to RTL when the language is one of
`nbi18n.RTL` (today: yi), and `Gtk.Grid` mirrors its columns with it. Nothing
told it not to, so the keypad came out backwards:

      LTR                              yi, as it shipped
      sqrt pi  7 8 9 div               div 9 8 7  pi sqrt
      x2   e   4 5 6 x                 x   6 5 4  e  x2
      1/x  x!  1 2 3 -                 -   3 2 1  x! 1/x
      +/-  %   0 . = +                 +  = . 0   %  +/-

Digits are written left to right in Yiddish, Hebrew and Arabic alike, and every
calculator sold into those markets has the standard Western pad. A mirrored one
is not a translation, it is a different machine — and one whose muscle memory is
wrong in a way that produces silently incorrect arithmetic rather than an error.

TWO SEPARATE FAULTS, and fixing the first left the second standing, which is why
this file checks them apart:

  * the GRID mirrored its columns  -> the block ran 9 8 7
  * the LABELS mirrored their glyphs -> under an RTL paragraph direction the
    bidi algorithm draws "(" as ")". The bracket keys swapped FACES while still
    inserting what they always had, so the key that looked like ")" typed "(".
    Pinning the grid does nothing about this; the faces need pinning too.

...and the readout, which is a number and must hug the same edge in every
language, because a figure grows leftward out of its units column as it is
typed. It was sitting against the left edge with the room to grow on the wrong
side.

WHAT IS DELIBERATELY *NOT* PINNED, and is checked here so the fix cannot quietly
grow into a blanket one: the kicker labels above the readout, the view bar and
the menus are TEXT. They are translated, they read right to left, and they must
keep doing so. The rule is that the INSTRUMENT is left-to-right and the CHROME
follows the language.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite run under
NB_LANG=yi and pointed at the copy with CALCULATOR_MODULE_DIR. All MEASURED:

  1. the keypad grids stop being pinned
     (`g.set_direction(Gtk.TextDirection.LTR)` -> `pass`)          5 FAILED
       FAIL 7 is left of 8 is left of 9
       FAIL 4 is left of 5 is left of 6
       FAIL 1 is left of 2 is left of 3
       FAIL 0 is left of the decimal point
       FAIL the function strip reads STO-> first

  2. the key FACES stop being pinned
     (`child.set_direction(Gtk.TextDirection.LTR)` -> `pass`)      2 FAILED
       FAIL the bracket keys are not bidi-mirrored
       FAIL ...and so is every other key face
     Note what stays GREEN: every position check. The grid is still pinned, so
     the geometry is right and the pad looks correct — the two bracket keys have
     simply swapped faces. This is exactly what the first fix left standing, and
     it is why the geometry and the glyphs are checked apart.

  3. the readout is left to the process direction
     (`self.disp_lbl.set_direction(...)` -> `pass`)                1 FAILED
       FAIL the readout hugs the same edge as in English

  4. the pin is OVER-applied and spreads to a text label
     (`kick.set_direction(Gtk.TextDirection.LTR)` added)           1 FAILED
       FAIL the kicker labels still read right-to-left
     The check that stops this fix becoming a different bug. Measured aside: a
     blanket `shell.set_direction(LTR)` on the root box does NOT reach these
     labels — the direction did not propagate down in this tree — so the
     realistic over-application is per-widget, and that is the one caught.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

# Must be set before nbapp is imported: it applies the direction AT IMPORT.
os.environ["NB_LANG"] = "yi"
import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-rtl-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import dialogshot                                             # noqa: E402
import nbapp                                                  # noqa: E402
import nbi18n                                                 # noqa: E402

W, H = 1024, 722
uishot.load_theme()
nbapp.screen_size = lambda: (W, H)
import calculator                                             # noqa: E402
dialogshot.install_app_css(calculator)

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


# The suite is worthless if the app is not actually in RTL — that is the whole
# premise. Fail loudly rather than pass a left-to-right app.
check("the process is in RTL for this language",
      "yi" in nbi18n.RTL
      and Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL,
      Gtk.Widget.get_default_direction())

app = calculator.Calculator()
child = app.get_child()
app.remove(child)
off = Gtk.OffscreenWindow()
off.set_size_request(W, H)
off.add(child)
off.show_all()
child.set_size_request(W, H)
for _ in range(80):
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def walk(w, out=None):
    out = [] if out is None else out
    out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            walk(c, out)
    return out


keys = {}
for kd, btn, face in app._buttons:
    keys[kd[0]] = (btn, face)


def x_of(label):
    btn = keys.get(label, (None, None))[0]
    return None if btn is None else btn.get_allocation().x


# ------------------------------------------- the number block is not mirrored
# Real geometry, read off the realised widgets: a check on KEYS or on the
# attach-columns would agree with the code that placed them and miss a
# direction flip entirely, because GTK mirrors at ALLOCATION time.
for trio in (("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3")):
    xs = [x_of(d) for d in trio]
    ok = None not in xs and xs[0] < xs[1] < xs[2]
    check("%s is left of %s is left of %s" % trio, ok,
          "x positions %s" % (dict(zip(trio, xs)),))

check("0 is left of the decimal point",
      None not in (x_of("0"), x_of(".")) and x_of("0") < x_of("."),
      (x_of("0"), x_of(".")))

check("the function strip reads STO-> first",
      None not in (x_of("STO→"), x_of("AC"))
      and x_of("STO→") < x_of("AC"),
      (x_of("STO→"), x_of("AC")))

# ------------------------------------------------- the faces are not mirrored
# The bidi algorithm draws "(" as ")" under an RTL paragraph direction. The
# label's own direction is what decides that, so it is what gets checked.
brackets = [keys.get("(", (None, None))[1], keys.get(")", (None, None))[1]]
check("the bracket keys are not bidi-mirrored",
      all(b is not None and b.get_direction() == Gtk.TextDirection.LTR
          for b in brackets),
      [None if b is None else b.get_direction() for b in brackets])

check("...and so is every other key face",
      all(f.get_direction() == Gtk.TextDirection.LTR
          for _kd, _b, f in app._buttons if isinstance(f, Gtk.Label)),
      [kd[0] for kd, _b, f in app._buttons
       if isinstance(f, Gtk.Label) and f.get_direction() != Gtk.TextDirection.LTR][:5])

# ------------------------------------------------------------- the readout
check("the readout hugs the same edge as in English",
      app.disp_lbl.get_direction() == Gtk.TextDirection.LTR,
      app.disp_lbl.get_direction())
check("...and so does the history line above it",
      app.hist_lbl.get_direction() == Gtk.TextDirection.LTR,
      app.hist_lbl.get_direction())

# ------------------------------------- but the CHROME still follows the language
# The check that keeps this fix from becoming a blanket one.
kicker = [w for w in walk(child)
          if isinstance(w, Gtk.Label)
          and w.get_style_context().has_class("disp-kicker")]
check("the kicker labels still read right-to-left", kicker
      and all(k.get_direction() != Gtk.TextDirection.LTR for k in kicker),
      [k.get_text() for k in kicker])

nav_btns = list(app._views.values())
check("...and so does the view bar",
      nav_btns and all(b.get_direction() != Gtk.TextDirection.LTR
                       for b in nav_btns),
      [b.get_direction() for b in nav_btns])

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
