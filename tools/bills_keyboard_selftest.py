#!/usr/bin/env python3
"""Escape leaves. It never acts — across every overlay this app can put up.

    tools/guestrun.sh python3 tools/bills_keyboard_selftest.py

`_on_key` was named by no suite. It carries the OS-wide rule in its own comment
— "Esc leaves, it does not act" — and a scar: "without this it closed the whole
app from under them", which is what a half-filled bill sheet plus one Escape
used to do.

FOUR OVERLAYS, and Escape has to mean the same thing in all of them: the add
sheet, the edit sheet, the payment sheet, and the question asked before an
export replaces a file already in Documents. The last is the one that would cost
most if it were wrong — Escape there must leave the file on disk exactly as it
was, and "the user pressed a key to get out" must never be read as consent to
overwrite.

MEASURED, NOTHING WAS WRONG. All five behaviours hold. This file pins them,
because they are the kind that hold by construction — Escape simply tears the
overlay down and never reaches a commit — and a refactor that routed Escape
through the same path as a Cancel BUTTON would look tidier and still be right,
while one that routed it through the primary action would look tidier and be a
disaster. Nothing said which.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. Escape stops taking the overlay down and falls through to the base
     handler — the exact scar the source comment records
     (`if ev.keyval == Gdk.KEY_Escape and self._close_overlay():`
      -> `if False and self._close_overlay():`)                 4 FAILED
       FAIL Escape closes a half-filled add sheet     <- still open
       FAIL Escape closes the payment sheet           <- still open
       FAIL Escape closes the edit sheet              <- still open
       FAIL Escape closes the replace question        <- still open
     The "Escape was CONSUMED" check does NOT fire under this mutation, and the
     reason is worth knowing: the base handler returns True as well, because it
     is busy closing the window. So that check distinguishes "handled" from
     "ignored" but NOT "handled here" from "handled by the thing that shuts the
     app" — the four checks above are what actually catch the scar. Kept, with
     its limit stated, rather than presented as the guard it is not.
  2. the Ctrl+Z branch is removed
     (the `self._undo_delete()` branch deleted from `_on_key`)   2 FAILED
       FAIL Ctrl+Z restores the deleted bill          <- 0
       FAIL ...with its payment history               <- []

WHAT NEITHER PROOF TURNS RED, said plainly: the "did not act" halves — no bill
created, no payment recorded, no edit saved, the file untouched. Escape cannot
reach a commit from where it sits, so no single-line mutation makes it act
without rewriting the handler into a different one. They are asserted anyway,
because they are the actual promise; proof 1 is what would catch the realistic
regression (Escape stops being handled here at all), and these say what must
still be true when someone changes how it IS handled.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-billskbd-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/bills.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("BILLS_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk                            # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402
import bills                                                  # noqa: E402

uishot.load_theme()
nbapp.screen_size = lambda: (1024, 768)

R = []
ESC = Gdk.KEY_Escape


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=900):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def key(val, state=0):
    return type("Event", (), {"keyval": val, "state": state})()


def widgets(w, kind, out=None):
    out = [] if out is None else out
    if isinstance(w, kind):
        out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            widgets(c, kind, out)
    return out


def fresh():
    with open(STORE, "w") as f:
        json.dump({"bills": [dict(id="m", payee="Rent", account="A1",
                                  amount=5000, due="2026-08-15", every=1,
                                  method="phone", address="", phone="555",
                                  note="", lead=0,
                                  paid=[{"on": "2026-07-15",
                                         "for": "2026-07-15", "amount": 5000,
                                         "method": "phone", "ref": "r1"}])]}, f)
    a = bills.Bills()
    pump()
    return a


def is_open(app):
    return getattr(app, "_overlay_card", None) is not None


def type_into(app, text):
    es = widgets(getattr(app, "_overlay_card", None), Gtk.Entry)
    if es:
        es[0].set_text(text)
    return es


# ------------------------------------------------ a half-filled ADD sheet
app = fresh()
before = len(app.bills)
app._open_form(None)
pump()
type_into(app, "Half typed payee")
check("the add sheet opens", is_open(app))
handled = app._on_key(app, key(ESC))
pump()
check("Escape closes a half-filled add sheet", not is_open(app), "still open")
check("...and no bill was created", len(app.bills) == before,
      (len(app.bills), before))
# The scar in `_on_key`'s comment is that Escape "closed the whole app from
# under them" — i.e. it fell through to the base handler. So the handler must
# CONSUME the event, not merely happen to leave the sheet shut. Asserting the
# return value is what distinguishes those two.
# (This check first read `app.get_child() is not None or True`, which cannot be
# false. Deleted rather than repaired: a check that cannot fail reads like
# coverage. Same slip as the accounting keyboard suite, two days running.)
check("...and Escape was CONSUMED, not passed to the base handler",
      handled is True, handled)
app.destroy()
pump()

# ------------------------------------------------------- the PAYMENT sheet
app = fresh()
paid_before = len(app._bill("m")["paid"])
app._open_payment("m")
pump()
type_into(app, "50.00")
check("the payment sheet opens", is_open(app))
app._on_key(app, key(ESC))
pump()
check("Escape closes the payment sheet", not is_open(app), "still open")
check("...and no payment was recorded",
      len(app._bill("m")["paid"]) == paid_before,
      len(app._bill("m")["paid"]))
check("...and the bill has not moved on",
      bills.due_info(app._bill("m"))["due"] == "2026-08-15",
      bills.due_info(app._bill("m"))["due"])
app.destroy()
pump()

# ---------------------------------------------------------- the EDIT sheet
app = fresh()
app._open_form(app._bill("m"))
pump()
type_into(app, "Renamed by mistake")
check("the edit sheet opens", is_open(app))
app._on_key(app, key(ESC))
pump()
check("Escape closes the edit sheet", not is_open(app), "still open")
check("...and the edit was discarded", app._bill("m")["payee"] == "Rent",
      app._bill("m")["payee"])
check("...and the payment history is untouched",
      len(app._bill("m")["paid"]) == 1, app._bill("m")["paid"])
app.destroy()
pump()

# ------------------------------------ the question before REPLACING a file
# The one that would cost most if it were wrong: pressing a key to get out must
# never be read as consent to overwrite what is already in Documents.
app = fresh()
docs = os.path.join(H, "Documents")
os.makedirs(docs, exist_ok=True)
decoy = os.path.join(docs, bills.PDF_NAME)
with open(decoy, "w") as f:
    f.write("DECOY")
app._export_pdf()
pump()
check("exporting over an existing file asks first", is_open(app),
      "no question was asked")
app._on_key(app, key(ESC))
pump()
check("Escape closes the replace question", not is_open(app), "still open")
with open(decoy) as f:
    kept = f.read()
check("...and the file on disk is exactly as it was", kept == "DECOY",
      repr(kept[:40]))
app.destroy()
pump()

# --------------------------------------------- Escape with nothing open
app = fresh()
app._on_key(app, key(ESC))
pump()
check("Escape with no overlay deletes nothing", len(app.bills) == 1,
      len(app.bills))

# ...and the one key that DOES act, acts.
app._do_delete("m")
pump()
check("deleting removes the bill", len(app.bills) == 0, len(app.bills))
app._on_key(app, key(Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK))
pump()
check("Ctrl+Z restores the deleted bill", len(app.bills) == 1, len(app.bills))
check("...with its payment history",
      bool(app.bills) and len(app._bill("m")["paid"]) == 1,
      app.bills and app._bill("m")["paid"])
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
