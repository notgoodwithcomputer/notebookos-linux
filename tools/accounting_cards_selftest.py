#!/usr/bin/env python3
"""Every overlay card must sit on a veil, and the veil must actually paint.

    tools/guestrun.sh python3 tools/accounting_cards_selftest.py

THE DEFECT. This app builds FOUR modal scrims — the row editor, the delete
confirm, the opening balance and the report card — and styled NONE of them.
There was no `.scrim` rule in the file at all, so all four cards floated over a
ledger at full contrast while bills, ebook, illustrator, installer, media,
music, novel, sequencer, settings, tasks and video all dim theirs. Accounting
was the outlier.

It matters more here than in a text app. A card covers the LEFT of the figures
behind it, so a row reading "$950.00" shows through as "50.00" and "$51.40" as
"51.40" — a delete confirmation drawn on top of a ledger displaying plausible
WRONG NUMBERS. The delete confirm therefore carries a heavier veil than the
other three, the distinction settings.py already draws between `.scrim` and its
confirm scrim, for this exact reason.

HOW THIS MEASURES: PIXELS, not properties. Asking whether the EventBox has the
class would pass with the CSS rule deleted, and asking whether it is "visible"
would pass with no background at all — an EventBox owns a GdkWindow and paints
NOTHING without one, which is the trap settings.py documents. So the window is
rendered to a real pixbuf and a background pixel far from the card is sampled
and compared against the same pixel with no card open. Measured at 1024x722:
undimmed paper is ~(250,248,242), the 0.18 veil lands at ~(211,210,207) and the
0.32 confirm veil at ~(180,179,176).

Two separate renders minutes apart were NOT distinguishable by eye — 211 against
180 looked identical — and the pixel values settled it. That is the whole reason
this file samples rather than eyeballs.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the scrims lose their class (`add_class("scrim")` deleted at all sites)
                                                                   4 FAILED
       FAIL the row editor darkens what is behind it
            <- background (252,251,248) is no darker than the undimmed
               (252,251,248)
       FAIL the opening balance card darkens what is behind it
       FAIL the report card darkens what is behind it
       FAIL every overlay scrim is given a scrim class

     THIS PROOF CORRECTED THIS FILE. That last check first read the repo copy of
     accounting.py by its expected path, so it stayed GREEN against a module
     that had every add_class stripped — it was reading past its own subject. It
     reads `accounting.__file__` now. A check that cannot go red is not a check.
  2. the `.scrim` rule is removed from the stylesheet
     (`.scrim { background: rgba(26,25,22,0.18); }` -> `.scrim { }`)
       the same three PIXEL failures — which is the point: the class alone
       proves nothing, because an EventBox with no background paints nothing.
  3. the confirm veil drops to the ordinary weight
     (`.confirmscrim { ... 0.32 }` -> `... 0.18`)
       FAIL the delete confirm is veiled more heavily than an ordinary card
            <- confirm 211, ordinary 211
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctcards-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/accounting.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import accounting                                             # noqa: E402

uishot.load_theme()

R = []
W, HGT = 1024, 722
# A point in the ledger's background, well clear of every card.
SX, SY = 850, 650


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=800):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def seed():
    with open(STORE, "w") as f:
        json.dump({"opening": 2400.0,
                   "tx": [{"date": "03 Jul", "iso": "2026-07-03",
                           "desc": "Rent", "amt": -950.0},
                          {"date": "21 Jul", "iso": "2026-07-21",
                           "desc": "Freelance invoice 115", "amt": 480.0},
                          {"date": "28 Jul", "iso": "2026-07-28",
                           "desc": "Groceries", "amt": -51.4}]}, f)


def sample(open_card):
    """Render the window with `open_card` applied and return the RGB of the
    background pixel at (SX, SY)."""
    seed()
    app = accounting.Accounting()
    pump()
    app._overlay_size = lambda: (W, HGT)
    off = Gtk.OffscreenWindow()
    kid = app.get_child()
    app.remove(kid)
    off.add(kid)
    off.set_size_request(W, HGT)
    off.show_all()
    pump()
    off.get_pixbuf()
    pump()
    if open_card is not None:
        open_card(app)
        pump()
    buf = off.get_pixbuf()
    pump()
    n, rs, data = buf.get_n_channels(), buf.get_rowstride(), buf.get_pixels()
    o = SY * rs + SX * n
    rgb = tuple(data[o:o + 3])
    off.destroy()
    app.destroy()
    pump()
    return rgb


def lum(rgb):
    return sum(rgb) / 3.0


base = sample(None)
check("the ledger's background is light with no card open", lum(base) > 230,
      base)

CARDS = (("the row editor", lambda a: a._edit_tx(0)),
         ("the opening balance card", lambda a: a._opening_card()),
         ("the report card", lambda a: a._report_summary()))

ordinary = None
for name, act in CARDS:
    got = sample(act)
    if ordinary is None:
        ordinary = lum(got)
    check("%s darkens what is behind it" % name, lum(got) < lum(base) - 12,
          "background %s is no darker than the undimmed %s" % (got, base))

confirm = sample(lambda a: a._confirm_delete(0))
check("the delete confirm darkens what is behind it",
      lum(confirm) < lum(base) - 12, (confirm, base))
# The one card whose background must not read as live figures: a card covers the
# LEFT of a row, so "$950.00" shows through as "50.00".
check("the delete confirm is veiled more heavily than an ordinary card",
      lum(confirm) < ordinary - 12,
      "confirm %d, ordinary %d" % (lum(confirm), ordinary))

# Every scrim must be a real one, not a bare EventBox: the class AND a rule that
# gives it a background. Checked as a pair because either alone is vacuous.
#
# Read the MODULE UNDER TEST, not the path this file expects it at. The first
# version opened the repo copy directly and so stayed green under a red proof
# that had stripped every add_class from the module actually being exercised —
# a check that reads past its own subject cannot go red, and a gate that cannot
# go red is not a gate.
src = open(accounting.__file__).read()
check("every overlay scrim is given a scrim class",
      src.count("scrim = Gtk.EventBox()")
      == src.count('add_class("scrim")') + src.count('add_class("confirmscrim")'),
      "%d EventBox scrims, %d classed"
      % (src.count("scrim = Gtk.EventBox()"),
         src.count('add_class("scrim")')
         + src.count('add_class("confirmscrim")')))
check("...and the stylesheet gives that class a background",
      ".scrim { background:" in src and ".confirmscrim { background:" in src)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
