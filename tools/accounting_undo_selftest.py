#!/usr/bin/env python3
"""Deleting a ledger entry can be taken back.

It could not be. `_confirm_delete`'s card said "This cannot be undone" and it
was telling the truth: a Delete key on a focused row, or the editor's Delete
button, removed a real financial record permanently. The app had no undo of any
kind — no history, no Edit-menu entries, no Ctrl+Z — in the one place in this OS
whose whole subject is being right about money.

Undo now spans the whole book (every entry and the opening balance), which is
the same shape academics uses, so one mechanism reverses an add, an edit and a
delete alike.

WHAT THIS SUITE INSISTS ON, beyond "undo exists":
  * the entry comes back with EVERY field intact, not just its description —
    an undo that restores a row while losing its `iso` has quietly changed the
    user's data while claiming to have restored it;
  * it comes back at its ORIGINAL POSITION, because a ledger's order is its
    running balance;
  * the change is PERSISTED, so an undo survives closing the window — an undo
    that only repaints the screen loses the entry again at the next autosave;
  * redo puts it back;
  * and the totals agree after every step, since a restored entry that does not
    reach the balance is worse than one that never came back.

RED PROOFS (M1), each mutation applied alone with the output measured:

  1. `_undo_snapshot` aliases the live entries instead of copying them
     (`{"tx": [dict(t) for t in self.tx]}` -> `{"tx": self.tx}`)
       FAIL undo brings the entry back
       FAIL ...at its original position
       FAIL ...with every field intact
       FAIL ...and the balance is what it was
       FAIL the restored entry is on disk, not just on screen
       FAIL and undo brings it back once more
     The snapshot shares the list, so the delete mutates the history that was
     supposed to reverse it. academics paid for this exact bug with its `meets`
     lists; it is the first thing to get wrong here.

  2. `_undo_restore` stops persisting (`self._autosave()` -> `pass`)
       FAIL the restored entry is on disk, not just on screen
     Everything on screen looks correct. The entry is gone again at the next
     autosave or when the window closes, which is the whole reason that check
     reads the file rather than the model.

  3. `_delete_tx` takes no checkpoint (`self.undo.checkpoint(...)` -> `pass`)
       FAIL the menu names the step it will reverse   <- Undo    Ctrl+Z
     Note what does NOT fail: undo still restores the entry perfectly. With no
     free-text buffer in this app there is no half-finished typing step for a
     checkpoint to flush, so the only thing it buys is the step's NAME. That is
     still worth having — "Undo Delete Entry" tells the reader what they are
     about to reverse and "Undo" asks them to guess — but a mutation that
     changes only the label is evidence about the label and nothing else.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctundo-%d" % os.getpid()
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


LEDGER = [
    {"date": "01 Aug", "iso": "2026-08-01", "desc": "Rent", "amt": -950.0},
    {"date": "02 Aug", "iso": "2026-08-02", "desc": "Salary", "amt": 2400.0},
    {"date": "03 Aug", "iso": "2026-08-03", "desc": "Groceries", "amt": -51.4},
]


def fresh():
    with open(STORE, "w") as f:
        json.dump({"opening": 100.0, "tx": [dict(t) for t in LEDGER]}, f)
    a = accounting.Accounting()
    pump()
    return a


def total(a):
    return round(a.opening + sum(t["amt"] for t in a.tx), 2)


# ------------------------------------------------------------ it exists at all
app = fresh()
check("the ledger has an undo history", hasattr(app, "undo"))
labels = [i[0] for i in app.menu_items("Edit") if i and i[0]]
check("Edit menu offers undo", any("undo" in str(l).lower() for l in labels),
      labels)
check("Edit menu offers redo", any("redo" in str(l).lower() for l in labels),
      labels)

# ------------------------------------------------------------------- a delete
before_total = total(app)
app._delete_tx(1)                       # the Salary credit, in the middle
pump()
check("deleting removes the entry", len(app.tx) == 2,
      [t["desc"] for t in app.tx])
check("...and the balance follows it", total(app) == round(before_total - 2400.0, 2),
      total(app))

app.undo.undo()
pump()
check("undo brings the entry back", len(app.tx) == 3,
      [t["desc"] for t in app.tx])
check("...at its original position", [t["desc"] for t in app.tx] ==
      ["Rent", "Salary", "Groceries"], [t["desc"] for t in app.tx])
check("...with every field intact", app.tx[1] == LEDGER[1], app.tx[1])
check("...and the balance is what it was", total(app) == before_total,
      (total(app), before_total))

# THE ONE THAT MATTERS: an undo that only repaints loses the entry again the
# moment anything autosaves, or the window closes.
with open(STORE) as f:
    on_disk = json.load(f)
check("the restored entry is on disk, not just on screen",
      len(on_disk.get("tx", [])) == 3,
      [t.get("desc") for t in on_disk.get("tx", [])])

# The step is NAMED in the menu. This is the only thing `undo.checkpoint()`
# actually buys in this app — there is no free-text buffer here, so there is no
# half-finished typing step for it to flush, and removing the checkpoint changes
# nothing about what undo RESTORES. It changes what the menu SAYS, and a menu
# reading "Undo" where it could read "Undo Delete Entry" is the difference
# between a reader knowing what they are about to reverse and guessing.
app.undo.redo()
pump()
lbl = [i[0] for i in app.menu_items("Edit")][0]
check("the menu names the step it will reverse", "Delete Entry" in str(lbl),
      lbl)
app.undo.undo()
pump()
app.undo.redo()
pump()
check("redo deletes it again", len(app.tx) == 2,
      [t["desc"] for t in app.tx])
app.undo.undo()
pump()
check("and undo brings it back once more", len(app.tx) == 3)
app.destroy()
pump()

# --------------------------------------------------------------------- an add
app = fresh()
n0 = len(app.tx)
app.add_entry("Bookshop", -22.99)
pump()
check("adding appends an entry", len(app.tx) == n0 + 1)
check("...and the menu names that step too",
      "Add Entry" in str([i[0] for i in app.menu_items("Edit")][0]),
      [i[0] for i in app.menu_items("Edit")][0])
app.undo.undo()
pump()
check("undo removes an added entry", len(app.tx) == n0,
      [t["desc"] for t in app.tx])
check("...and the balance goes back too", total(app) == 100.0 + sum(
    t["amt"] for t in LEDGER), total(app))
app.destroy()
pump()

# -------------------------------------------------------------------- an edit
app = fresh()
app._edit_tx(0)
pump()
if hasattr(app, "_e_desc"):
    app._e_desc.set_text("Rent, August")
    app._e_amt.set_text("960")
    app._e_date.set_text("01 Aug")
    app._save_edit()
    pump()
    check("editing changes the entry", app.tx[0]["desc"] == "Rent, August",
          app.tx[0])
    app.undo.undo()
    pump()
    check("undo restores the whole original entry", app.tx[0] == LEDGER[0],
          app.tx[0])
else:
    check("the editor opened", False, "no _e_desc")

# ------------------------------------------------- undo does not eat the book
# The empty-ledger case: undoing back past the first step must not leave the
# store empty on disk when the user still has entries on screen.
app.undo.undo()
pump()
check("undoing past the start leaves the ledger intact", len(app.tx) == 3,
      [t["desc"] for t in app.tx])
with open(STORE) as f:
    check("...and the file still holds them",
          len(json.load(f).get("tx", [])) == 3)
app.destroy()
pump()

# ------------------------------------------- the app must not deny its own undo
# The delete-confirm card read "Delete X? This cannot be undone." That was true
# when it was written. Adding the undo history made it FALSE, and false in the
# frightening direction: it tells somebody a reversible action is permanent, so
# they keep a row they meant to remove. Whoever adds undo to an app is the
# person who has to go and find the copy that says there is none.
app = fresh()
app._overlay_size = lambda: (1024, 722)
app._confirm_delete(1)
pump()


def overlay_text(w, out=None):
    if out is None:
        out = []
    if isinstance(w, Gtk.Label):
        out.append(w.get_text() or "")
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            overlay_text(c, out)
    return out


said = " ".join(overlay_text(app._confirm_layer)) if getattr(
    app, "_confirm_layer", None) else ""
check("the delete card actually opened", bool(said.strip()), repr(said))
check("the delete card does not claim the deletion is permanent",
      "cannot be undone" not in said.lower()
      and "can not be undone" not in said.lower(), repr(said))
check("...and it still names what is being deleted", "Salary" in said,
      repr(said))
app._close_confirm()
pump()
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
