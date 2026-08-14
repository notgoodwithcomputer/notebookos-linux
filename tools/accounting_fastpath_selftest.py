#!/usr/bin/env python3
"""Adding an entry inserts ONE row — and the result is identical either way.

    tools/guestrun.sh python3 tools/accounting_fastpath_selftest.py

Adding an entry rebuilt every row on screen. Measured on a 600-entry ledger:
153 ms per add — 44 ms building 150 row widgets, the rest destroying the old
ones and letting GTK settle — for a change that touches exactly one row.
Appending cannot alter any existing row: a running balance is the total AFTER
that entry, so earlier rows keep theirs, and every existing entry's
chronological index is unchanged because the new one goes on the end.

`_append_one_row` takes that shortcut and returns False whenever it is not
plainly valid, leaving `_refresh()` to do the work.

THE ONLY THING THAT LICENSES A SHORTCUT IS THAT IT CHANGES NOTHING. So the
central check here is not "is it faster" — it is that the widget tree after the
fast path is INDISTINGUISHABLE from the tree a full rebuild produces: the same
number of rows, the same cell texts in the same order, the same footer. A
performance change in a money app that alters a single printed figure is a
worse bug than the slowness it cured.

Speed is asserted only as a loose ceiling (the fast path must not be doing a
full rebuild's worth of work), never as a wall-clock number — a timing
threshold on shared hardware is a flaky gate, and this suite must be able to
fail for the right reason only.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the fast path forgets to trim the page back to `_shown`
     (the `[self._shown:]` trim loop deleted)
       FAIL the page never grows beyond one page of rows
            <- 151 rows on screen, page is 150
       FAIL fast and full rebuild agree on a paged ledger
            <- row count 151 vs 150
  2. the new row is appended at the BOTTOM instead of position 0
     (`self.rows.reorder_child(row, 0)` deleted)
       FAIL fast and full rebuild agree on a short ledger
            <- row 0 differs: 'Entry 0' vs 'NEWEST'
  3. the fast path runs even while a search is filtering
     (`if self._terms or self.search.get_text().strip()` -> `if False`)
       FAIL a filtered ledger falls back to the full rebuild
            <- the fast path claimed a filtered view
"""
import os
import sys
import json
import time
import shutil

H = "/tmp/nbhome-acctfast-%d" % os.getpid()
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
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import accounting                                             # noqa: E402

uishot.load_theme()

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=3000):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def build(n, opening=1000.0):
    with open(STORE, "w") as f:
        json.dump({"opening": opening,
                   "tx": [{"date": "0%d Aug" % (1 + i % 9),
                           "iso": "2026-08-%02d" % (1 + i % 28),
                           "desc": "Entry %d" % i,
                           "amt": -12.34 if i % 2 else 250.0}
                          for i in range(n)]}, f)
    a = accounting.Accounting()
    pump()
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


def tree(app):
    """Everything about the rows that a person could see, in order."""
    out = []
    for w in app.rows.get_children():
        cells = []

        def walk(x):
            if isinstance(x, Gtk.Label):
                cells.append(x.get_text() or "")
            if isinstance(x, Gtk.Container):
                for c in x.get_children():
                    walk(c)
        walk(w)
        out.append((w.get_style_context().has_class("morerow"), tuple(cells)))
    return out


def sidebar(app):
    return (app.balance.get_text(), app.credit_lbl.get_text(),
            app.debit_lbl.get_text(), app.count_lbl.get_text(),
            app.opening_lbl.get_text())


# ------------------------------------------------- the shortcut is really taken
app, off = build(20)
check("the fast path is taken on an ordinary add", app._append_one_row() is True)
off.destroy()
app.destroy()
pump()

# ------------------------------------------------- and refused when it must be
app, off = build(20)
app.search.set_text("Entry 1")
app._search_timeout()
pump()
check("a filtered ledger falls back to the full rebuild",
      app._append_one_row() is False, "the fast path claimed a filtered view")
app.search.set_text("")
app._search_timeout()
pump()
off.destroy()
app.destroy()
pump()

app, off = build(0)
check("an empty ledger falls back to the full rebuild",
      app._append_one_row() is False, "the fast path claimed an empty ledger")
off.destroy()
app.destroy()
pump()

# ------------------------------------------------------------ THE EQUIVALENCE
# For each ledger size: add an entry the fast way, record the tree; then force a
# full rebuild and record it again. They must be identical.
for n, label in ((0, "an empty ledger"), (1, "a one-entry ledger"),
                 (20, "a short ledger"), (149, "one row short of a page"),
                 (150, "exactly one page"), (151, "one row over a page"),
                 (400, "a paged ledger")):
    app, off = build(n)
    app.add_entry("NEWEST", -7.77)
    pump()
    fast_tree, fast_side = tree(app), sidebar(app)
    app._refresh()
    pump()
    full_tree, full_side = tree(app), sidebar(app)

    check("fast and full rebuild agree on %s" % label, fast_tree == full_tree,
          "row count %d vs %d%s" % (
              len(fast_tree), len(full_tree),
              "" if len(fast_tree) != len(full_tree) else
              "; first difference: %r" % ([(a, b) for a, b in
                                           zip(fast_tree, full_tree)
                                           if a != b][:1])))
    check("...and the sidebar figures agree on %s" % label,
          fast_side == full_side, (fast_side, full_side))
    if n:
        check("...and the newest entry is the top row on %s" % label,
              fast_tree and "NEWEST" in " ".join(fast_tree[0][1]),
              fast_tree[0] if fast_tree else None)
    check("the page never grows beyond one page of rows on %s" % label,
          len([r for r in fast_tree if not r[0]]) <= app._PAGE,
          "%d rows on screen, page is %d"
          % (len([r for r in fast_tree if not r[0]]), app._PAGE))
    off.destroy()
    app.destroy()
    pump()

# ------------------------------------- the running balance is still the truth
# Checked against an independent summation, never against another of the app's
# own numbers.
app, off = build(30)
for i in range(6):
    app.add_entry("Extra %d" % i, -3.5 if i % 2 else 9.25)
    pump()
want = round(1000.0 + sum(t["amt"] for t in app.tx), 2)
check("the balance after six fast adds is the opening plus every entry",
      app.balance.get_text() == app._money(want),
      (app.balance.get_text(), app._money(want)))
top = tree(app)[0][1]
check("...and the top row prints that same balance",
      app._money(want) in top, top)
off.destroy()
app.destroy()
pump()

# --------------------------------------------------------------- and it is faster
# A LOOSE ceiling, not a wall-clock threshold: the fast path must not be doing a
# full rebuild's worth of work. Anything under half is a comfortable margin on
# shared hardware and cannot flake the way a fixed millisecond budget would.
app, off = build(600)
t0 = time.time()
app._refresh()
pump()
full_ms = (time.time() - t0) * 1000
t0 = time.time()
app.add_entry("Timed", -1.0)
pump()
fast_ms = (time.time() - t0) * 1000
print("     (full rebuild %.0f ms, fast add %.0f ms on 600 entries)"
      % (full_ms, fast_ms))
check("adding an entry costs well under a full rebuild",
      fast_ms < full_ms * 0.5,
      "fast %.0f ms against a %.0f ms rebuild" % (fast_ms, full_ms))
off.destroy()
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
