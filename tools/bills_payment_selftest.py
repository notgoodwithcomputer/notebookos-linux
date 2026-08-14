#!/usr/bin/env python3
"""Recording a payment: once, against the right month, or not at all.

    tools/guestrun.sh python3 tools/bills_payment_selftest.py

`bills_flows_selftest` already walks the happy path — the sheet opens, a payment
is recorded, it reaches disk, the bill moves to its next due date. This file
takes the paths either side of that: the ways a person can commit the sheet more
than once, and the amounts they can put in it.

WHY DOUBLE-CLICK IS THE CHECK THAT MATTERS HERE. Recording a payment ADVANCES a
recurring bill to its next occurrence. So a second commit does not write a
duplicate that somebody would notice — it files a payment against NEXT MONTH and
quietly marks a bill paid that has not been paid. An impatient double-click on a
button labelled "Record Payment" is an ordinary thing to do, and the cost of it
here is a missed bill a month later, discovered by a late fee.

Measured: the app is DEFENDED — one, two and three clicks all record exactly one
payment, and Enter-in-the-amount-field followed by a click likewise.

THE MECHANISM IS NOT WHAT IT LOOKS LIKE, and this file first said so wrongly.
`_commit` calls `_close_overlay()` before it appends the payment, which reads
like the guard — it is not. Moving that call BELOW the append changes nothing,
because the sheet is torn down on commit either way and the second click has no
button left to press. Proved: that mutation left all 24 checks green. The guard
is that the sheet CLOSES AT ALL, not where in `_commit` it closes. A refactor
that kept the sheet up — to show a confirmation in place, say — would reopen the
hole no matter how carefully the ordering was preserved.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the sheet is left open after a payment is filed
     (`self._close_overlay()` removed from `_commit`)           3 FAILED
       FAIL a double-click records exactly one payment
            <- 2 payments, settling ['2026-08-15', '2026-08-15']
       FAIL a triple-click records exactly one payment
            <- 3 payments, settling ['2026-08-15', ...]
       FAIL Enter in the amount field, then a click, records one payment
            <- 2 payments, settling ['2026-08-15', '2026-08-15']
     Note the duplicates settle the SAME occurrence, not consecutive months:
     `settles` is captured once when the sheet opens. So the real failure is a
     doubled payment against one month, and the bill still advances only one
     occurrence — which is why a check on the due date alone would miss it.
  2. a non-numeric amount is accepted (`if raw and cents is None:` -> `if False:`)
                                                                2 FAILED
       FAIL a non-numeric amount records nothing   <- 1 payment recorded
       FAIL ...and the sheet stays open to say so
            <- the sheet closed on a refusal
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-billspay-%d" % os.getpid()
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
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402
import bills                                                  # noqa: E402

uishot.load_theme()
# bills sizes its detail column from the screen; offscreen that is the HOST
# monitor. Pin the guest panel (see APP-LOOP.md step 3).
nbapp.screen_size = lambda: (1024, 768)

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=900):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def widgets(w, kind, out=None):
    out = [] if out is None else out
    if isinstance(w, kind):
        out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            widgets(c, kind, out)
    return out


def fresh(amount=5000, every=1, due="2026-08-15"):
    with open(STORE, "w") as f:
        json.dump({"bills": [dict(id="m", payee="Rent", account="",
                                  amount=amount, due=due, every=every,
                                  method="phone", address="", phone="",
                                  note="", lead=0, paid=[])]}, f)
    a = bills.Bills()
    pump()
    return a


def confirm_button(app):
    """The sheet's own confirm button — searched INSIDE the overlay card only.

    The detail pane behind the sheet carries its own "Record Payment" button
    (the one that OPENS this sheet), and a whole-window search finds that one
    first: clicking it re-opened the sheet instead of committing, so every
    payment check read as "nothing was recorded". A defect in the probe that
    looks exactly like a defect in the app."""
    card = getattr(app, "_overlay_card", None)
    if card is None:
        return None
    for b in widgets(card, Gtk.Button):
        if (b.get_label() or "") in ("Record Payment", "Record payment",
                                     "Done"):
            return b
    return None


def open_sheet(app, text=None):
    app._open_payment("m")
    pump()
    card = getattr(app, "_overlay_card", None)
    es = widgets(card, Gtk.Entry) if card is not None else []
    if text is not None and es:
        es[0].set_text(text)
    return es


# ------------------------------------------------- one commit, one payment
for label, clicks in (("a single click", 1), ("a double-click", 2),
                      ("a triple-click", 3)):
    app = fresh()
    open_sheet(app, "50.00")
    btn = confirm_button(app)
    check("the payment sheet offers a confirm button (%s)" % label,
          btn is not None)
    if btn is not None:
        for _ in range(clicks):
            btn.clicked()
            pump()
        bill = app._bill("m")
        fors = [p["for"] for p in bill["paid"]]
        check("%s records exactly one payment" % label, len(bill["paid"]) == 1,
              "%d payments, settling %s" % (len(bill["paid"]), fors))
        check("...and the bill advances exactly one occurrence",
              bills.due_info(bill)["due"] == "2026-09-15",
              "due %s, expected 2026-09-15" % bills.due_info(bill)["due"])
    app.destroy()
    pump()

# Enter in the amount field commits too, so Enter-then-click is the same trap
# by another route.
app = fresh()
es = open_sheet(app, "50.00")
es[0].emit("activate")
pump()
btn = confirm_button(app)
if btn is not None:
    btn.clicked()
    pump()
bill = app._bill("m")
check("Enter in the amount field, then a click, records one payment",
      len(bill["paid"]) == 1,
      "%d payments, settling %s" % (len(bill["paid"]),
                                    [p["for"] for p in bill["paid"]]))
app.destroy()
pump()

# ------------------------------------------------------------- the amount
app = fresh()
open_sheet(app, "not a number")
confirm_button(app).clicked()
pump()
check("a non-numeric amount records nothing",
      len(app._bill("m")["paid"]) == 0,
      "%d payment recorded" % len(app._bill("m")["paid"]))
check("...and the sheet stays open to say so",
      confirm_button(app) is not None, "the sheet closed on a refusal")
app.destroy()
pump()

# A bill whose figure varies opens with an EMPTY amount on purpose, and an empty
# amount is a legitimate record: the payment happened even if the figure was not
# typed. It must not be read as a refusal.
app = fresh(amount=None)
open_sheet(app, "")
confirm_button(app).clicked()
pump()
bill = app._bill("m")
check("a varies bill records a payment with a blank amount",
      len(bill["paid"]) == 1, bill["paid"])
if bill["paid"]:
    check("...stored as no figure rather than as zero",
          bill["paid"][0]["amount"] is None, bill["paid"][0]["amount"])
    check("...and it still settles the right occurrence",
          bill["paid"][0]["for"] == "2026-08-15", bill["paid"][0]["for"])
app.destroy()
pump()

# ---------------------------------------------------- what a payment settles
app = fresh()
open_sheet(app, "50.00")
confirm_button(app).clicked()
pump()
bill = app._bill("m")
p = bill["paid"][0]
check("the payment names the occurrence it settles",
      p["for"] == "2026-08-15", p)
check("...carries the bill's method", p["method"] == "phone", p["method"])
check("...and the figure that was typed", p["amount"] == 5000, p["amount"])
check("a recurring bill moves to its next occurrence",
      bills.due_info(bill)["due"] == "2026-09-15",
      bills.due_info(bill)["due"])
app.destroy()
pump()

# A ONE-OFF has nowhere to advance to: it is simply settled.
app = fresh(every=0)
open_sheet(app, "50.00")
confirm_button(app).clicked()
pump()
bill = app._bill("m")
info = bills.due_info(bill)
check("a one-off bill is settled by its payment", info["kind"] == "settled",
      info["kind"])
check("...and reads as Paid", info["state"] == "Paid", info["state"])
check("...and no longer needs paying", bills.needs_paying(info) is False)
app.destroy()
pump()

# Two DELIBERATE recordings pay two months, which is right — the second sheet is
# opened against the next occurrence. This is the behaviour the double-click
# checks above must not be confused with.
app = fresh()
for _ in range(2):
    open_sheet(app, "50.00")
    confirm_button(app).clicked()
    pump()
bill = app._bill("m")
check("two deliberate recordings settle two different occurrences",
      [p["for"] for p in bill["paid"]] == ["2026-08-15", "2026-09-15"],
      [p["for"] for p in bill["paid"]])
check("...and the bill is then due the month after",
      bills.due_info(bill)["due"] == "2026-10-15",
      bills.due_info(bill)["due"])
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
