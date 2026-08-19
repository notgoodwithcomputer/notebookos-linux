#!/usr/bin/env python3
"""Contacts: where the highlight lands after a card is deleted.

  delete-selects-the-row-that-takes-its-place
        self.active is an index into the STORE and the list beside it is
        sorted by name, so after a delete the highlight went to whichever
        card happened to inherit that store position. Deleting the fifth row
        of six selected the second — a card the reader was not looking at,
        with its whole record on the pane beside it, one keystroke away from
        being deleted in turn.

Run:  tools/guestrun.sh python3 tools/contacts_delete_selection_selftest.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, DE)

WORK = tempfile.mkdtemp(prefix="contacts-delsel-")
os.environ["NB_HOME"] = os.path.join(WORK, "home")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")

import nbapp  # noqa: E402
nbapp._APP_DIR = os.path.join(WORK, "nb-apps")
nbapp.APP_DIR = nbapp._APP_DIR
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import contacts as con  # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def seed(*names):
    # Deliberately stored in an order that is NOT the sorted order, which is
    # the whole point: a store index is not a row.
    people = [con.normalize_person({"name": n}, i)
              for i, n in enumerate(names)]
    os.makedirs(con.CFG_DIR, exist_ok=True)
    nbapp.atomic_write_json(con.CONTACTS_FILE, {"people": people})
    return con.Contacts()


def listed(win):
    return [p["name"] for _i, p in win._visible_order_pairs()]


def selected(win):
    return win.people[win.active]["name"] if win.people else None


win = seed("Marguerite Okonkwo", "Dr Anaya Prakash", "Tomasz Wisniewski",
           "Emile Eluard", "Zed Zane", "Oyvind Dahl")
rows = listed(win)
# rows: Dr Anaya Prakash, Emile Eluard, Marguerite Okonkwo, Oyvind Dahl,
#       Tomasz Wisniewski, Zed Zane
win.active = next(i for i, p in enumerate(win.people)
                  if p["name"] == rows[4])          # the fifth row
win._do_delete()
check("delete-selects-the-row-that-takes-its-place",
      selected(win) == rows[5], "row 5 of %r deleted; now on %r"
      % (rows, selected(win)))

# ...and deleting the LAST row falls back to the one before it rather than
# jumping to the top of the book.
rows = listed(win)
win.active = next(i for i, p in enumerate(win.people)
                  if p["name"] == rows[-1])
win._do_delete()
check("...and the last row falls back to the one above it",
      selected(win) == rows[-2], "last of %r deleted; now on %r"
      % (rows, selected(win)))

# The undo contract is untouched: Ctrl+Z puts the card back and selects it.
win._undo_delete()
check("...and undo still restores the card and selects it",
      selected(win) == rows[-1], selected(win))

# One card left: deleting it empties the book without an index error.
while len(win.people) > 1:
    win._do_delete()
win._do_delete()
check("...and deleting the only card empties the book",
      win.people == [] and win.active == 0,
      (win.people, win.active))
win.destroy()

bad = R.count(False)
print("RESULT: %s (%d checks, %d failed)"
      % ("ALL PASS" if not bad else "SOME FAILED", len(R), bad))
sys.exit(1 if bad else 0)
