#!/usr/bin/env python3
"""One ledger, driven end to end, the way a person would drive it.

Every other accounting suite tests a mechanism — the money arithmetic, the CSV,
the salvage path, undo. This one tests the JOIN: open on nothing, build a book
up, live in it, correct it, break bits of it, take those back, export, close,
and open it again — asserting after EVERY step that the ledger is still true.

The bugs this shape catches are the ones no unit check sees, because they only
appear when one operation leaves state the next one reads differently. Two found
on day 2 were exactly that: editing an entry's date left the machine-readable
date behind so the CSV exported two different days, and a salvaged ledger came
back without its opening balance so every figure was out by it.

THE INVARIANT, checked after every step:

    headline BALANCE == opening + sum(amounts) == the chart's last point

all three to the cent. A ledger that disagrees with itself is the one failure
this app cannot have, and the three are computed by three different code paths
(`_refresh`, plain summation, `_balance_series`) so agreeing is evidence.

ZERO IS THE VALUE THAT HIDES AN ADDEND. The first version of this suite ran the
whole session at `opening == 0.0`, and every mutation touching the opening
balance was therefore invisible to it — breaking `_refresh` to drop the opening,
and breaking `_balance_series` to start from zero, both left all 24 checks
green. A walkthrough that never gives a term a non-zero value is not exercising
the arithmetic that uses it. The session now opens at 2400.00.

RED PROOFS (M1), measured against the version that opens at 2400:

  1. `_refresh` forgets the opening balance
     (`round(self.opening + sum(...))` -> `round(0 * self.opening + sum(...))`)
       FAIL after loading an opening balance the ledger agrees with itself
       FAIL after adding five entries the ledger agrees with itself
       FAIL after searching ... and every later step

  2. `_balance_series` starts from zero
     (`vals = [self.opening]; b = self.opening` -> `[0.0]; b = 0.0`)
       ...the same list. The chart and the headline stop agreeing, which is
       exactly what a reader would see and never be able to explain.

Both produced 24 checks / 0 failed before the opening balance was seeded.
"""
import os
import sys
import csv
import json
import shutil

H = "/tmp/nbhome-acctsess-%d" % os.getpid()
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
from gi.repository import Gtk, Gdk                            # noqa: E402
import accounting                                             # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=500):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def truth(app, step):
    """The three routes to the balance must give one answer."""
    summed = round(app.opening + sum(t["amt"] for t in app.tx), 2)
    shown = app.balance.get_text()
    series = app._balance_series()
    charted = round(series[-1], 2) if series else None
    ok = (shown == app._money(summed)) and (charted == summed)
    check("after %s the ledger agrees with itself" % step, ok,
          "summed=%r shown=%r chart=%r" % (summed, shown, charted))
    return summed


# ------------------------------------------------------------- a fresh install
app = accounting.Accounting()
pump()
check("a fresh install opens with nothing", app.tx == [] and app.opening == 0.0,
      (app.tx, app.opening))
truth(app, "opening empty")
app.destroy()
pump()

# A NON-ZERO OPENING BALANCE, for the rest of the session.
# The first version of this suite ran the whole thing at opening == 0.0, and
# every mutation involving the opening balance was therefore INERT against it:
# breaking `_refresh` to forget the opening, and breaking `_balance_series` to
# start from zero, both left all 24 checks green. Zero is the value that hides
# an addend. The ledger below opens at 2400.00 so those paths are actually
# carrying something.
with open(STORE, "w") as fh:
    json.dump({"opening": 2400.0, "tx": []}, fh)
app = accounting.Accounting()
pump()
check("the opening balance is loaded", app.opening == 2400.0, app.opening)
truth(app, "loading an opening balance")

# ------------------------------------------------------------- build it up
ENTRIES = [("Rent", -950.0), ("Salary", 2400.0), ("Groceries", -51.4),
           ("Bookshop", -22.99), ("Freelance invoice 114", 625.0)]
for desc, amt in ENTRIES:
    app.add_entry(desc, amt)
pump()
check("every entry landed", len(app.tx) == len(ENTRIES),
      [t["desc"] for t in app.tx])
total = truth(app, "adding five entries")
check("the total is the opening plus what was entered",
      total == round(2400.0 + sum(a for _d, a in ENTRIES), 2), total)

# --------------------------------------------------------------------- find
app.search.set_text("rent")
app._search_timeout()
pump()
matched = [t for t in app.tx if app._matches(t, ("rent",))]
check("a search narrows to the right entries",
      [t["desc"] for t in matched] == ["Rent"], [t["desc"] for t in matched])
truth(app, "searching")
app.search.set_text("")
app._search_timeout()
pump()
truth(app, "clearing the search")

# --------------------------------------------------------------------- edit
app._edit_tx(0)
pump()
app._e_desc.set_text("Rent, August")
app._e_amt.set_text("960")
app._e_date.set_text("01 Aug")
app._save_edit()
pump()
check("the edit landed", app.tx[0]["desc"] == "Rent, August", app.tx[0])
check("...and the amount kept its direction", app.tx[0]["amt"] == -960.0,
      app.tx[0]["amt"])
truth(app, "editing an entry")

# ------------------------------------------------- flipping debit and credit
# The editor's direction control changes the SIGN of a real financial record,
# and the coverage audit found nothing had ever exercised it. Getting it wrong
# silently turns money out into money in — an error of twice the amount, in the
# direction that flatters the balance.
app._edit_tx(0)
pump()
was = app.tx[0]["amt"]
check("the editor opens on the entry's own direction",
      app._edir == ("debit" if was < 0 else "credit"), (app._edir, was))
# NB: `total` is a plain float further up this file (the running result of a
# check), so the helper name is shadowed. Compute it directly rather than
# quietly calling a number.
before_flip = round(app.opening + sum(t["amt"] for t in app.tx), 2)
app._e_set_dir("credit" if was < 0 else "debit")
app._save_edit()
pump()
check("flipping the direction flips the sign", app.tx[0]["amt"] == -was,
      (was, app.tx[0]["amt"]))
after_flip = round(app.opening + sum(t["amt"] for t in app.tx), 2)
check("...and the balance moves by twice the amount",
      after_flip == round(before_flip - 2 * was, 2),
      (before_flip, after_flip))
truth(app, "flipping a direction")
app.undo.undo()
pump()
check("undo puts the direction back", app.tx[0]["amt"] == was,
      app.tx[0]["amt"])
truth(app, "undoing a direction flip")

# ------------------------------------------------------------------- delete
gone = app.tx[2]["desc"]
app._delete_tx(2)
pump()
check("the deleted entry is gone",
      gone not in [t["desc"] for t in app.tx], [t["desc"] for t in app.tx])
truth(app, "deleting an entry")

app.undo.undo()
pump()
check("undo brings it back", gone in [t["desc"] for t in app.tx],
      [t["desc"] for t in app.tx])
truth(app, "undoing the delete")

# ------------------------------------------------------------------- export
docs = os.path.join(H, "Documents")
app._export_csv()
pump()
files = sorted(f for f in os.listdir(docs) if f.endswith(".csv"))
check("a CSV was written", bool(files), os.listdir(docs))
if files:
    with open(os.path.join(docs, files[-1]), encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    body = [r for r in rows[1:] if r and any(r)]
    check("the CSV has one row per entry", len(body) == len(app.tx),
          (len(body), len(app.tx)))
    # The exported running balance must end where the app says the balance is.
    try:
        last = float(body[-1][-1])
    except (ValueError, IndexError):
        last = None
    check("the CSV's last balance is the app's balance",
          last == round(app.opening + sum(t["amt"] for t in app.tx), 2),
          (last, round(app.opening + sum(t["amt"] for t in app.tx), 2)))

app._export_pdf()
pump()
pdfs = [f for f in os.listdir(docs) if f.endswith(".pdf")]
check("a PDF was written", bool(pdfs), os.listdir(docs))
truth(app, "exporting")

# ------------------------------------------------ close it and open it again
before = ([dict(t) for t in app.tx], app.opening)
app._on_destroy()
app.destroy()
pump()

again = accounting.Accounting()
pump()
check("every entry survived closing and reopening",
      len(again.tx) == len(before[0]),
      (len(again.tx), len(before[0])))
check("...and each one is unchanged", again.tx == before[0],
      [t for t in again.tx if t not in before[0]])
truth(again, "reopening")

# ...and the file agrees with what is on screen.
with open(STORE) as fh:
    disk = json.load(fh)
check("the file on disk matches the ledger in memory",
      len(disk.get("tx", [])) == len(again.tx)
      and disk.get("opening") == again.opening,
      (len(disk.get("tx", [])), disk.get("opening")))
again.destroy()
pump()

# ---------------------------------------------- driving it without a mouse
# Esc is a CHAIN, and the order is the whole design: the thing most recently
# put in front of you closes first. Nothing pinned that order, and it is easy to
# break by adding a new overlay anywhere but the top of the chain. (It also
# caught me out while probing: with the entry form open, Esc closes the FORM and
# leaves the search alone — which looked like "Esc does not clear the search"
# until I checked what was actually on screen.)
again = accounting.Accounting()
pump()
again._overlay_size = lambda: (1024, 722)
again.add_entry("Rent", -950.0)
pump()


def esc(app):
    ev = Gdk.EventKey()
    ev.keyval = Gdk.KEY_Escape
    ev.state = 0
    return app._on_key(app, ev)


again.search.set_text("rent")
again._search_timeout()
pump()
check("Esc clears an active search when nothing else is open",
      esc(again) and again.search.get_text() == "",
      repr(again.search.get_text()))

again.search.set_text("rent")
again._search_timeout()
pump()
again._reveal_form()
pump()
esc(again)
pump()
check("...but with the entry form open, Esc closes the FORM first",
      not again.form_reveal.get_reveal_child()
      and again.search.get_text() == "rent",
      (again.form_reveal.get_reveal_child(), again.search.get_text()))
esc(again)
pump()
check("...and the next Esc then clears the search",
      again.search.get_text() == "", repr(again.search.get_text()))

again._edit_tx(0)
pump()
esc(again)
pump()
check("Esc closes an open row editor before anything else",
      getattr(again, "_edit_layer", None) is None)

# Enter commits the form from either field — a form you can only submit with the
# mouse is not a form you can use.
again._reveal_form()
pump()
n0 = len(again.tx)
again.f_desc.set_text("Coffee")
again.f_amt.set_text("3.50")
again.f_amt.emit("activate")
pump()
check("Enter in the amount field commits the entry", len(again.tx) == n0 + 1,
      len(again.tx))
n0 = len(again.tx)
again.f_desc.set_text("Tea")
again.f_amt.set_text("2.00")
again.f_desc.emit("activate")
pump()
check("Enter in the description field commits it too",
      len(again.tx) == n0 + 1, len(again.tx))
again.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
