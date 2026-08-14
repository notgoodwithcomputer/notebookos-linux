#!/usr/bin/env python3
"""The store keeps what this version does not understand.

    tools/guestrun.sh python3 tools/bills_extra_selftest.py

THE DEFECT. Opening bills and letting it save DESTROYED every field the current
build does not have a name for — no user action, no warning, no way back.
Measured on a store carrying three extra fields on a bill (`category`,
`reconciled`, `sort_hint`) and two at the top (`schema`, `ledger_name`):

    LOST FROM THE USER'S FILE: category, reconciled, sort_hint,
                               schema, ledger_name

That is the READ-SIDE loss class — the worst kind this project has found,
because nothing the person did caused it and nothing told them. There is no
network and no cloud here: the file under the user's home IS the only copy.

ONE DEFECT, THREE PLACES, and each had to be fixed separately — a fix at any one
of them still lost the data at the next:

  * `normalise()` built a fresh dict per record, so the keys were gone the
    moment the file was READ, before anything was edited or saved.
  * the edit sheet's `_commit` rebuilt the record from the fields it can reach,
    so editing a bill dropped them even once the loader kept them.
  * `_save` wrote `{"bills": ...}`, so the store's own top-level keys went on
    the first save whatever the records held.

WHAT THIS IS FOR, concretely: a store written by a newer build, a field added by
a future release, or anything a person put there by hand. Accounting had the
identical defect in two of its own three places (task 046, defects 8 and the
Codex edit-path fix); this is the same class found again in a different app, so
it is worth checking wherever an app rebuilds a record rather than updating one.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the loader stops carrying unknown fields
     (the `for key, value in raw.items()` carry-through removed from
     `normalise`)
       FAIL a bill's unknown fields survive being read   <- category gone
       FAIL ...and survive an edit
       FAIL ...and are on disk after a save
  2. the edit rebuilds the record from the sheet alone
     (the `for key, value in target.items()` merge removed from `_commit`)
       FAIL a bill's unknown fields survive an edit
            <- {'category': None, 'reconciled': None, 'sort_hint': None}
  3. the save drops the store's own keys
     (`payload = dict(self._extra ...)` -> `payload = {}`)
       FAIL the store's unknown top-level keys survive a save
            <- ['bills'] on disk, expected schema and ledger_name too

THE VIEW MENU'S ORDER rides in the same store and through the same round-trip,
which is why it is guarded here rather than in a file of its own. It was applied
correctly and never written down: pick "By Payee", close the app, reopen, and
the list was back in due order — a preference accepted, acted on, and forgotten.

  4. choosing an order does not write it down
     (`self._save()` removed from `_set_sort`)                  3 FAILED
       FAIL ...and it reaches the file at once, without waiting for a close
       FAIL the chosen order survives closing and reopening   <- due
       FAIL ...and the list really is in that order
            <- ['Zebra Water', 'Apple Energy']
  5. the stored order is ignored at startup
     (the `_extra`-backed restore -> `self.sort = "due"`)       2 FAILED
       FAIL the chosen order survives closing and reopening   <- due
  6. a stored order is trusted without checking it
     (`stored if stored in self.SORTS else "due"` -> `stored or "due"`)
                                                               3 FAILED
       FAIL a stored order of 'sideways' falls back to due   <- sideways
       FAIL a stored order of 7 falls back to due            <- 7
       FAIL a stored order of ['payee'] falls back to due    <- ['payee']

CHANGING THE PAYMENT METHOD hides rows in the bill sheet — an address and a
posting lead for post, a phone number for post and phone, a note for everything
EXCEPT post. So switching method makes a filled-in field vanish from the sheet,
and it must not vanish from the BILL. The rows are hidden rather than destroyed
and `_commit` reads every widget while they are all still alive. That is also
what makes a note on a POSTED bill reachable at all — set it under another
method, then switch — which is the case day 3's defect 2 fixed on the printed
side, so the two guard the same user's data from opposite ends.

  7. the commit blanks the note when the method does not show it
     (`"note": note.get_text().strip()` ->
      `... if method["id"] != "mail" else ""`)
       FAIL ...and the NOTE survived the row being hidden   <- ''
  8. the commit blanks the address when the method does not show it
     (`"address": addr.strip()` -> `... if method["id"] == "mail" else ""`)
       FAIL ...and the ADDRESS survived the row being hidden   <- ''

     Both are the "tidy up the fields this method does not use" edit a later
     reader would think is obviously correct, and both silently destroy data
     the person typed.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-billsextra-%d" % os.getpid()
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
nbapp.screen_size = lambda: (1024, 768)

R = []
EXTRA_BILL = {"category": "Housing", "reconciled": True, "sort_hint": 7}
EXTRA_TOP = {"schema": 9, "ledger_name": "Household"}


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


def seed():
    bill = dict(id="m", payee="Rent", account="A1", amount=5000,
                due="2026-08-15", every=1, method="phone", address="",
                phone="", note="", lead=0, paid=[])
    bill.update(EXTRA_BILL)
    doc = {"bills": [bill]}
    doc.update(EXTRA_TOP)
    with open(STORE, "w") as f:
        json.dump(doc, f)


def missing(d, want):
    return dict((k, d.get(k)) for k in want if d.get(k) != want[k])


# ------------------------------------------------------------- read
seed()
app = bills.Bills()
pump()
loaded = app._bill("m")
check("a bill's unknown fields survive being read",
      not missing(loaded, EXTRA_BILL), missing(loaded, EXTRA_BILL))

# The known fields still win: an unknown key cannot smuggle a bad value past the
# normaliser, and a colliding key must not overwrite the validated one.
check("...without displacing the fields the app validates",
      loaded["amount"] == 5000 and loaded["payee"] == "Rent"
      and loaded["method"] == "phone",
      (loaded["amount"], loaded["payee"], loaded["method"]))

# ------------------------------------------------------------- edit
app._open_form(app._bill("m"))
pump()
card = getattr(app, "_overlay_card", None)
check("the edit sheet opens", card is not None)
if card is not None:
    es = widgets(card, Gtk.Entry)
    if es:
        es[0].set_text("Rent revised")
    save = [b for b in widgets(card, Gtk.Button)
            if (b.get_label() or "") in ("Save", "Save Bill", "Done")]
    check("...and offers a save button", bool(save))
    if save:
        save[-1].clicked()
        pump()
    edited = app._bill("m")
    check("the edit took", edited["payee"] == "Rent revised", edited["payee"])
    check("a bill's unknown fields survive an edit",
          not missing(edited, EXTRA_BILL), missing(edited, EXTRA_BILL))
    check("...and the payment history is still its own",
          isinstance(edited.get("paid"), list), edited.get("paid"))

# ------------------------------------------------------------- save
app._save()
pump()
with open(STORE) as f:
    disk = json.load(f)
check("the store's unknown top-level keys survive a save",
      not missing(disk, EXTRA_TOP),
      "%s on disk, expected schema and ledger_name too" % sorted(disk))
check("...and the bills are still there", len(disk.get("bills", [])) == 1,
      len(disk.get("bills", [])))
dbill = disk["bills"][0]
check("...and a bill's unknown fields are on disk after a save",
      not missing(dbill, EXTRA_BILL), missing(dbill, EXTRA_BILL))
# "sort" is the app's OWN key (the View menu's order, persisted since day 3), so
# it is expected alongside the store's unknown ones — the check is that nothing
# BEYOND those appears.
check("no unexplained top-level key was invented",
      set(disk) == set(EXTRA_TOP) | {"bills", "sort"}, sorted(disk))
app.destroy()
pump()

# ------------------------------------- and they survive a full reopen cycle
app = bills.Bills()
pump()
again = app._bill("m")
check("everything is still there after closing and reopening",
      not missing(again, EXTRA_BILL) and again["payee"] == "Rent revised",
      (missing(again, EXTRA_BILL), again["payee"]))
app.destroy()
pump()

# A store with NO extra keys must not gain any — the carry-through has to be
# invisible when there is nothing to carry.
with open(STORE, "w") as f:
    json.dump({"bills": [dict(id="p", payee="Plain", account="", amount=100,
                              due="2026-08-15", every=1, method="phone",
                              address="", phone="", note="", lead=0,
                              paid=[])]}, f)
app = bills.Bills()
pump()
app._save()
pump()
with open(STORE) as f:
    plain = json.load(f)
check("a store with nothing extra gains only the app's own sort key",
      set(plain) == {"bills", "sort"}, sorted(plain))
check("...and its bill gains nothing either",
      set(plain["bills"][0]) == {"id", "payee", "account", "amount", "due",
                                 "every", "method", "address", "phone",
                                 "note", "lead", "paid"},
      sorted(plain["bills"][0]))
app.destroy()
pump()

# ------------------------------------------- the View menu's order is a choice
# It was applied correctly and never written down: pick "By Payee", close the
# app, reopen, and the list was back in due order. A preference accepted, acted
# on, and forgotten — the class this OS has recorded as "settings must leave the
# process". It rides in the same store as the bills, so it round-trips through
# the same `_extra` path the rest of this file guards.
with open(STORE, "w") as f:
    json.dump({"bills": [
        dict(id="a", payee="Zebra Water", account="", amount=900,
             due="2026-08-20", every=1, method="phone", address="", phone="",
             note="", lead=0, paid=[]),
        dict(id="b", payee="Apple Energy", account="", amount=5000,
             due="2026-08-25", every=1, method="phone", address="", phone="",
             note="", lead=0, paid=[])]}, f)
app = bills.Bills()
pump()
check("a fresh store opens in due order", app.sort == "due", app.sort)
check("...and the View menu offers all three orders",
      len(app.menu_items("View")) == len(bills.Bills.SORTS),
      [l for l, _c in app.menu_items("View")])

app._set_sort("payee")
pump()
check("choosing an order applies it",
      [b["payee"] for b, _i in app._ordered()] == ["Apple Energy",
                                                   "Zebra Water"],
      [b["payee"] for b, _i in app._ordered()])
with open(STORE) as f:
    check("...and it reaches the file at once, without waiting for a close",
          json.load(f).get("sort") == "payee")
app.destroy()
pump()

app = bills.Bills()
pump()
check("the chosen order survives closing and reopening", app.sort == "payee",
      app.sort)
check("...and the list really is in that order",
      [b["payee"] for b, _i in app._ordered()] == ["Apple Energy",
                                                   "Zebra Water"],
      [b["payee"] for b, _i in app._ordered()])
app.destroy()
pump()

# A hand-edited or newer value must not put the list into an order `_ordered`
# has no branch for.
for bad_sort in ("sideways", "", None, 7, ["payee"]):
    with open(STORE) as f:
        doc = json.load(f)
    doc["sort"] = bad_sort
    with open(STORE, "w") as f:
        json.dump(doc, f)
    app = bills.Bills()
    pump()
    check("a stored order of %r falls back to due" % (bad_sort,),
          app.sort == "due", app.sort)
    app.destroy()
    pump()

# --------------------------------- changing the method hides fields, not data
# The bill sheet shows a different set of rows per payment method: an address
# and a posting lead for post, a phone number for post and phone, a note for
# everything EXCEPT post. So switching method makes a filled-in field vanish
# from the sheet — and the question is whether it vanishes from the BILL.
#
# It must not. The rows are hidden, not destroyed, and `_commit` reads every
# widget while they are all still alive. This is also what makes a note on a
# posted bill reachable at all (set it under another method, then switch), which
# is the case day 3's defect 2 fixed on the printed side.


def open_sheet_and_switch(app, to_label):
    app._open_form(app._bill("m"))
    pump()
    card = getattr(app, "_overlay_card", None)
    segs = [b for b in widgets(card, Gtk.Button)
            if b.get_style_context().has_class("bl-seg")]
    hit = [b for b in segs if (b.get_label() or "") == to_label]
    if hit:
        hit[0].clicked()
        pump()
    save = [b for b in widgets(card, Gtk.Button)
            if (b.get_label() or "") in ("Save", "Save Bill", "Done")]
    if save:
        save[-1].clicked()
        pump()
    return bool(hit) and bool(save)


def one_bill(**kw):
    b = dict(id="m", payee="Rent", account="A1", amount=5000,
             due="2026-08-15", every=1, method="phone", address="",
             phone="555-0100", note="Quote 88", lead=0, paid=[])
    b.update(kw)
    with open(STORE, "w") as f:
        json.dump({"bills": [b]}, f)
    a = bills.Bills()
    pump()
    return a


app = one_bill()
check("the sheet switches a phone bill to post",
      open_sheet_and_switch(app, "By post"))
moved = app._bill("m")
check("...the method really changed", moved["method"] == "mail",
      moved["method"])
check("...and the NOTE survived the row being hidden",
      moved["note"] == "Quote 88", moved["note"])
app.destroy()
pump()

app = one_bill(method="mail", address="Acme\nPO Box 1", lead=5, note="")
check("the sheet switches a posted bill to phone",
      open_sheet_and_switch(app, "By phone"))
moved = app._bill("m")
check("...the method really changed", moved["method"] == "phone",
      moved["method"])
check("...and the ADDRESS survived the row being hidden",
      moved["address"] == "Acme\nPO Box 1", repr(moved["address"]))
check("...and so did the posting lead", moved["lead"] == 5, moved["lead"])
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
