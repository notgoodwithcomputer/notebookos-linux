#!/usr/bin/env python3
"""A signed figure keeps its sign, in a right-to-left interface too.

    tools/guestrun.sh python3 tools/accounting_rtl_selftest.py

Yiddish ships (`nbi18n.RTL == {"yi"}`) and `nbapp` calls
`Gtk.Widget.set_default_direction(RTL)` for it, which flips widget order for the
whole process. Nothing had ever run this app in that state.

THE DEFECT. A leading "+" or MINUS is a bidi-WEAK character (class ES) and the
figure after it is a run of European numerals (EN). In an RTL paragraph the
Unicode bidi algorithm resolves that weak sign to the paragraph direction and
lays it out on the FAR SIDE of the number. Measured under yi, before the fix:

    label holds '+$1,105.00'   ->  Pango drew  '$1,105.00+'
    label holds '-$1,974.39'   ->  Pango drew  '$1,974.39-'

Unsigned figures were unaffected, which is why this hides: the balance headline,
the opening balance and the running-balance column all looked perfectly correct
while every credit and every debit had its sign on the wrong end. In a ledger
the minus is the only thing on the row that says which way the money went.

Fixed by wrapping the finished signed string in U+2066 LEFT-TO-RIGHT ISOLATE ..
U+2069 POP DIRECTIONAL ISOLATE, which unlike an LRM cannot leak its direction
into the text around it, and which is applied only when the direction actually
in force is RTL — so the other sixteen languages get the same
string they always got, byte for byte.

WHAT THIS MEASURES. The VISUAL order Pango resolves, by asking the laid-out
layout where each logical character landed on the x axis. Asserting that the
isolate character is PRESENT would only be checking that the fix was applied;
it would pass just as well if the isolate did nothing, and it would go on
passing if Pango's behaviour changed underneath. The character check is kept as
a second, separate assertion, never as the only one.

`Accounting._ltr` now DELEGATES to `nbi18n.ltr` -- the same method promoted
OS-wide once the campaign found the shape in eleven other apps, with
tools/rtl_check.py as the static guard. It is kept as a method here rather than
inlined at the seven call sites so this file has a name in ITS OWN module to
mutate: a red proof must never have to reach into a campaign-owned file.

The static gate and this file check different things and both are needed. The
gate reads source and can only flag the high-confidence shape; a figure composed
at runtime is invisible to it. This file lays text out through Pango and reads
where the glyphs actually land.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the isolate is not applied (`return nbi18n.ltr(s)` -> `return s`,
     i.e. the defect itself restored)                          6 FAILED
       FAIL RTL: the credit's sign stays in front of the figure
            <- laid out as '$1,105.00+'
       FAIL RTL: ...and the figure itself is unchanged
            <- ('$1,105.00+', '+$1,105.00')
       FAIL RTL: the debit's sign stays in front of the figure
            <- laid out as '$950.00-'
       FAIL RTL: ...and the figure itself is unchanged
       FAIL RTL: a credit in the ledger column keeps its sign in front
            <- laid out as '$1,105.00+'
       FAIL RTL: the find summary's net keeps its sign in front
            <- laid out as '$155.00+'
     Note the three UNSIGNED checks stay green throughout, which is exactly how
     this defect hid in plain sight: the balance headline, the opening balance
     and the running-balance column were all correct.
  2. the sidebar figures stop going through `_ltr` at their CALL SITE
     (`self._ltr("+" + self._money(credit))` -> `("+" + self._money(credit))`),
     which is the regression a future edit is most likely to introduce -- the
     helper stays correct and one caller quietly stops using it
       FAIL RTL: the credit's sign stays in front of the figure
            <- laid out as '$1,105.00+'
       FAIL RTL: ...and the figure itself is unchanged
       FAIL RTL: the signed figures carry a directional isolate
            <- '+$1,105.00'

Both mutations are applied to a COPY of accounting.py. Neither reaches into
nbi18n, which is campaign-owned -- that is the reason `_ltr` survives as a thin
method here instead of being inlined at its call sites.

THE SAME SHAPE EXISTS ELSEWHERE IN THE OS, at lower stakes: `calendar.py` and
`widgets.py` build `_t("+%d more")` and `language.py` builds `_t("+%d XP")`, all
of which put a weak sign in front of European numerals in a label. Reported to
the campaign rather than fixed here -- those are other lanes' files.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctrtl-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/accounting.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("ACCOUNTING_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo               # noqa: E402
import cairo                                                   # noqa: E402
import uishot                                                  # noqa: E402
import nbi18n                                                  # noqa: E402
import accounting                                              # noqa: E402

uishot.load_theme()

R = []
_SURF = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 60)
_CR = cairo.Context(_SURF)
ISO = ("⁦", "⁩")


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=800):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def laid_out(s, rtl):
    """The order the characters really appear in, left to right, once Pango has
    resolved the bidi run at the given paragraph direction. Isolate marks are
    dropped from the result: they are controls, not glyphs."""
    lay = PangoCairo.create_layout(_CR)
    lay.set_text(s, -1)
    lay.set_auto_dir(False)
    lay.get_context().set_base_dir(
        Pango.Direction.RTL if rtl else Pango.Direction.LTR)
    lay.context_changed()
    placed = []
    for i, ch in enumerate(s):
        r = lay.index_to_pos(len(s[:i].encode("utf-8")))
        placed.append((r.x, ch))
    placed.sort()
    return "".join(c for _x, c in placed if c not in ISO)


def build(rtl):
    Gtk.Widget.set_default_direction(
        Gtk.TextDirection.RTL if rtl else Gtk.TextDirection.LTR)
    with open(STORE, "w") as f:
        json.dump({"opening": 2400.0,
                   "tx": [{"date": "01 Aug", "iso": "2026-08-01",
                           "desc": "Rent", "amt": -950.0},
                          {"date": "08 Aug", "iso": "2026-08-08",
                           "desc": "Salary", "amt": 1105.0}]}, f)
    a = accounting.Accounting()
    pump()
    return a


def money_cell(app, cls):
    def walk(w, out):
        if isinstance(w, Gtk.Label) and w.get_style_context().has_class(cls):
            out.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c, out)
    out = []
    for r in app.rows.get_children():
        walk(r, out)
    return [w.get_text() for w in out if (w.get_text() or "").strip()]


# Yiddish is the reason this file exists; if it stops being RTL the whole
# premise is gone and every check below would pass vacuously.
check("the shipped language set still contains an RTL language",
      bool(nbi18n.RTL), nbi18n.RTL)
check("...and it is Yiddish", "yi" in nbi18n.RTL, nbi18n.RTL)

# ------------------------------------------------------------------ LTR first
app = build(False)
credit_ltr = app.credit_lbl.get_text()
check("LTR: a signed figure is left exactly as it was",
      credit_ltr == "+" + app._money(1105.0),
      "%r is not %r" % (credit_ltr, "+" + app._money(1105.0)))
check("LTR: no isolate character is introduced",
      not any(c in credit_ltr for c in ISO), repr(credit_ltr))
check("LTR: the sign is in front where it belongs",
      laid_out(credit_ltr, False).startswith("+"),
      "laid out as %r" % laid_out(credit_ltr, False))
app.destroy()
pump()

# ------------------------------------------------------------------ then RTL
app = build(True)
cases = (("the credit", app.credit_lbl.get_text(), "+"),
         ("the debit", app.debit_lbl.get_text(), accounting.MINUS))
for name, text, sign in cases:
    got = laid_out(text, True)
    check("RTL: %s's sign stays in front of the figure" % name,
          got.startswith(sign), "laid out as %r" % got)
    check("RTL: ...and the figure itself is unchanged",
          got.lstrip(sign) == app._money(
              1105.0 if name == "the credit" else 950.0).lstrip(
                  accounting.MINUS),
          (got, text))

# The ledger's own credit column, not only the sidebar.
cells = money_cell(app, "txcredit")
check("RTL: a credit in the ledger column keeps its sign in front",
      cells and laid_out(cells[0], True).startswith("+"),
      "laid out as %r" % (laid_out(cells[0], True) if cells else None))

# The find summary's net.
app.search.set_text("a")
app._search_timeout()
pump()
if app.findsum.get_visible() and app.find_net.get_text():
    net = app.find_net.get_text()
    sign = net.lstrip("".join(ISO))[:1]
    if sign in ("+", accounting.MINUS):
        check("RTL: the find summary's net keeps its sign in front",
              laid_out(net, True).startswith(sign),
              "laid out as %r" % laid_out(net, True))
app.search.set_text("")
app._search_timeout()
pump()

# An UNSIGNED figure must not be wrapped for nothing, and must still read right.
bal = app.balance.get_text()
check("RTL: an unsigned figure is not wrapped",
      not any(c in bal for c in ISO), repr(bal))
check("RTL: ...and it still lays out correctly",
      laid_out(bal, True) == bal, (laid_out(bal, True), bal))

# The isolate is present as well as effective — kept as a SECOND assertion, so
# that no check in this file rests on the character alone.
check("RTL: the signed figures carry a directional isolate",
      all(c in app.credit_lbl.get_text() for c in ISO),
      repr(app.credit_lbl.get_text()))

app.destroy()
pump()
Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
