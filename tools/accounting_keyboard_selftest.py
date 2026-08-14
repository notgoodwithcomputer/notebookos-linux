#!/usr/bin/env python3
"""The ledger from the keyboard, and the OS-wide rule that Esc never deletes.

    tools/guestrun.sh python3 tools/accounting_keyboard_selftest.py

`_row_key` and `_on_key`'s Escape chain were named by no suite. That chain is
load-bearing and ordered on purpose — confirm, then editor, then report, then
the entry form, then the search — and every link in it is a place where the
wrong answer either strands somebody in a card they cannot leave or, worse,
destroys an entry on a key that must never destroy anything.

THIS FILE REPLACES A STUB. A dispatched run left a placeholder here that exited
non-zero unconditionally, on the premise that GTK could not initialize under
`tools/guestrun.sh`. Measured, that premise is false — `Gtk.init_check()[0]` is
True there and the offscreen pixbuf renders — so the stub was a permanently red
suite in the tree for a problem that did not exist. Reporting a blocked
measurement honestly is right; the blockage has to be real.

SYNTHETIC EVENTS. GTK will not deliver real key presses to an offscreen window,
so handlers are called directly with an object carrying `keyval` and `state`.
That tests the handler, NOT the binding — a handler that is never connected
would still pass here, so the connection itself is asserted separately from the
widget.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY of
accounting.py:

  1. Delete on a row deletes instead of asking
     (`self._confirm_delete(idx)` -> `self.tx.pop(idx); self._refresh()`)
       FAIL Delete on a row ASKS rather than removing
            <- the entry was gone: 2 entries left of 3
       FAIL ...and Esc never deletes: the entry is still there   <- (2, 3)

     The first attempt at this mutation called `_do_confirmed_delete(idx)`,
     which takes no index — the suite died with a TypeError and the harness
     printed nothing, so it read as a clean run with no failures. A red proof
     that CRASHES proves nothing and must never look like a pass; the harness
     now prints "SUITE CRASHED ... this is NOT a red proof". Hardening this file
     to `getattr(app, "_confirm_layer", None)` came out of the same run: under a
     regression that never opens the card, the attribute does not exist, and a
     suite that raises there reports nothing at all instead of the failure.
  2. Escape falls through the confirm card
     (`if self._close_confirm(): return True` deleted)
       FAIL Escape closes the delete confirm
            <- the confirm card is still open
  3. Escape stops closing the entry form
     (`nbtransitions.reveal(self.form_reveal, False)` -> `pass`)
       FAIL Escape closes the open entry form   <- the form is still revealed
  4. the Escape chain loses its ORDER: the search is cleared before the form is
     closed (the `if self._terms:` block moved above the form block)
       FAIL Escape closes the form BEFORE it clears the search
            <- search cleared while the form was still open
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctkbd-%d" % os.getpid()
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
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk                            # noqa: E402
import uishot                                                 # noqa: E402
import accounting                                             # noqa: E402

uishot.load_theme()

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=800):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def key(val, state=0):
    return type("Event", (), {"keyval": val, "state": state})()


ESC = Gdk.KEY_Escape


def build(n=3):
    with open(STORE, "w") as f:
        json.dump({"opening": 1000.0,
                   "tx": [{"date": "0%d Aug" % (i + 1),
                           "iso": "2026-08-0%d" % (i + 1),
                           "desc": "Entry %d" % i, "amt": -10.0 - i}
                          for i in range(n)]}, f)
    a = accounting.Accounting()
    pump()
    a._overlay_size = lambda: (1024, 722)
    off = Gtk.OffscreenWindow()
    kid = a.get_child()
    a.remove(kid)
    off.add(kid)
    off.set_size_request(1024, 722)
    off.show_all()
    pump()
    off.get_pixbuf()
    pump()
    return a, off


def rows(a):
    return [w for w in a.rows.get_children() if isinstance(w, Gtk.Button)]


# ------------------------------------------------- a row is reachable at all
app, off = build()
rs = rows(app)
check("the ledger builds focusable rows",
      bool(rs) and all(r.get_can_focus() for r in rs),
      [r.get_can_focus() for r in rs])

# ------------------------------------- Delete on a row ASKS, it does not delete
before = len(app.tx)
handled = app._row_key(rs[0], key(Gdk.KEY_Delete), 0)
pump()
check("Delete on a row is handled", handled is True, handled)
check("Delete on a row ASKS rather than removing",
      len(app.tx) == before
      and getattr(app, "_confirm_layer", None) is not None,
      "the entry was gone: %d entries left of %d" % (len(app.tx), before))

# THE OS-WIDE RULE. Esc only ever LEAVES.
app._on_key(app, key(ESC))
pump()
check("Escape closes the delete confirm",
      getattr(app, "_confirm_layer", None) is None,
      "the confirm card is still open")
check("...and Esc never deletes: the entry is still there",
      len(app.tx) == before, (len(app.tx), before))

# A key that is not Delete is passed on rather than swallowed.
check("an unrelated key on a row is not swallowed",
      app._row_key(rs[0], key(Gdk.KEY_a), 0) is False)
off.destroy()
app.destroy()
pump()

# ------------------------------------------------- Escape leaves every overlay
for name, opener, is_open in (
        ("the row editor", lambda a: a._edit_tx(0),
         lambda a: getattr(a, "_edit_layer", None) is not None),
        ("the opening balance card", lambda a: a._opening_card(),
         lambda a: getattr(a, "_edit_layer", None) is not None),
        ("the report card", lambda a: a._report_summary(),
         lambda a: getattr(a, "_report_layer", None) is not None)):
    app, off = build()
    n_before = len(app.tx)
    opener(app)
    pump()
    check("%s opens" % name, is_open(app))
    app._on_key(app, key(ESC))
    pump()
    check("Escape closes %s" % name, not is_open(app), "still open")
    check("...and leaves the ledger alone", len(app.tx) == n_before,
          (len(app.tx), n_before))
    off.destroy()
    app.destroy()
    pump()

# ------------------------------------------------------------- the entry form
app, off = build()
app._toggle_form()
pump()
check("the entry form opens", app.form_reveal.get_reveal_child())
app._on_key(app, key(ESC))
pump()
check("Escape closes the open entry form",
      not app.form_reveal.get_reveal_child(), "the form is still revealed")

# ------------------------------------------------------ and then the search
app.search.set_text("Entry 1")
app._search_timeout()
pump()
check("a search filters the ledger", bool(app._terms), app._terms)
app._on_key(app, key(ESC))
pump()
check("Escape clears the search once nothing is layered over it",
      not app._terms and app.search.get_text() == "", app.search.get_text())

# THE ORDER MATTERS. With BOTH a form open and a search running, one Escape must
# close the FORM and leave the search alone — a person backing out of a
# half-typed entry has not asked for their filter to be thrown away too.
app.search.set_text("Entry 1")
app._search_timeout()
pump()
app._toggle_form()
pump()
check("both a form and a search are active", app.form_reveal.get_reveal_child()
      and bool(app._terms))
app._on_key(app, key(ESC))
pump()
check("Escape closes the form BEFORE it clears the search",
      not app.form_reveal.get_reveal_child() and bool(app._terms),
      "search cleared while the form was still open"
      if not app._terms else "the form is still open")
off.destroy()
app.destroy()
pump()

# ------------------------------------------- undo reaches the window, not a widget
# Ctrl+Z is bound at the window so it works from the ledger, the form and the
# FIND box alike; a binding on the focused widget would work in only one of them.
app, off = build()
n_before = len(app.tx)
app.add_entry("Bookshop", -22.99)
pump()
check("an entry is added", len(app.tx) == n_before + 1)
app._on_key(app, key(Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK))
pump()
check("Ctrl+Z from the window undoes it", len(app.tx) == n_before,
      (len(app.tx), n_before))

# --------------------------------- a card must not outlive the list it points at
# Every overlay card captures a row INDEX into self.tx, and Ctrl+Z is bound at
# the WINDOW so it fires straight through the modal scrim. If undo reshapes the
# ledger while a card is open, that captured index now names a different entry —
# and the card's next action is a DELETE. This is the academics index-remap class
# aimed at somebody's money.
#
# The app is defended, and by design rather than by luck: undo closes any open
# card. Nothing pinned that, so a refactor could quietly drop it and the defect
# would be silent until it deleted the wrong row.
for name, opener, still_open in (
        ("the delete confirm", lambda a, i: a._confirm_delete(i),
         lambda a: getattr(a, "_confirm_layer", None) is not None),
        ("the row editor", lambda a, i: a._edit_tx(i),
         lambda a: getattr(a, "_edit_layer", None) is not None)):
    app, off = build()
    app.add_entry("DELTA", -4.0)
    pump()
    names = [t["desc"] for t in app.tx]
    i = names.index("Entry 1")
    opener(app, i)
    pump()
    check("%s opens on a known row" % name, still_open(app))
    app._on_key(app, key(Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK))
    pump()
    check("an undo through the scrim reshapes the ledger",
          [t["desc"] for t in app.tx] == names[:-1],
          [t["desc"] for t in app.tx])
    check("...and %s is closed rather than left on a stale index" % name,
          not still_open(app),
          "the card survived an undo and still points at index %d of a list "
          "that changed under it" % i)
    off.destroy()
    app.destroy()
    pump()

# A SEARCH, by contrast, must NOT close the confirm — it changes only what is
# displayed, and the captured index refers to self.tx, not to the filtered view.
# Closing it would be a nuisance; acting on the filtered position would delete
# the wrong entry.
app, off = build()
names = [t["desc"] for t in app.tx]
i = names.index("Entry 2")
app._confirm_delete(i)
pump()
app.search.set_text("Entry 0")
app._search_timeout()
pump()
check("a search leaves an open confirm alone",
      getattr(app, "_confirm_layer", None) is not None)
app._do_confirmed_delete()
pump()
left = [t["desc"] for t in app.tx]
check("...and confirming still deletes the row it was opened for",
      "Entry 2" not in left and len(left) == len(names) - 1, left)
off.destroy()
app.destroy()
pump()

# The handler must be CONNECTED, not merely correct: every check above calls it
# directly, so a row whose key-press-event was never wired would pass all of
# them. `inspect.getsource` reads the module actually imported, so this follows
# a red proof into the mutated copy instead of reading past it.
#
# (An earlier version of this check also asked `rs[0].get_events() != 0 or True`
# — which cannot be false. It was removed rather than repaired: a check that
# cannot fail is worse than no check, because it reads like coverage.)
import inspect                                                # noqa: E402
src = inspect.getsource(accounting.Accounting._tx_row)
check("a ledger row's key handler is wired in _tx_row to _row_key",
      'connect("key-press-event", self._row_key' in src,
      "no key-press-event connection in _tx_row")
off.destroy()
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
