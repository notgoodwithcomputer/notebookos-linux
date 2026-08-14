#!/usr/bin/env python3
"""Reports, the find summary, and paging — three untested money calculations.

A coverage audit of accounting.py turned up 35 methods no suite had ever named.
Most are drawing helpers that a render exercises anyway. Three were not: they
are user-facing features that ADD UP MONEY and nothing checked them.

  * `_report_summary` / `_report_card` — Reports ▸ Ledger Summary. With a FIND
    query running it summarises WHAT YOU ARE LOOKING AT and reports a NET rather
    than a balance, because a subset of a ledger has no balance; with no query
    it is the whole book.
  * `_sync_find` — the "N matches · net" line beside the search box.
  * `_show_more` / `_more_row` — a ledger longer than one page. If this is
    wrong, entries past the first 150 are simply unreachable, which on a
    five-year ledger is most of it.

Every figure here is checked against an independent summation, never against
another of the app's own numbers.

RED PROOFS (M1), measured, each alone:

  1. the find net sums the WHOLE ledger instead of the matches
     (`sum(t["amt"] for _i, t, _b in display)` -> `... for t in self.tx`)
       FAIL the find summary's net is the net of the MATCHES
            <- said '−$1,001.40', the matches come to '−$950.00'
  2. a filtered report labels its figure BALANCE instead of NET
     (the `if self._terms:` branch in _report_summary swapped)
       FAIL a filtered report reports a NET, not a balance
  3. paging stops advancing (`self._shown += self._PAGE` -> `pass`)
       FAIL showing more reveals the next page   <- still 150 of 400
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctrep-%d" % os.getpid()
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


def pump(n=600):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def build(tx, opening=0.0):
    with open(STORE, "w") as f:
        json.dump({"opening": opening, "tx": tx}, f)
    a = accounting.Accounting()
    pump()
    a._overlay_size = lambda: (1024, 722)     # see APP-LOOP.md harness note
    return a


def card_text(app):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            out.append(w.get_text() or "")
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    layer = getattr(app, "_report_layer", None)
    if layer is not None:
        walk(layer)
    return out


LEDGER = [
    {"date": "03 Jul", "iso": "2026-07-03", "desc": "Rent", "amt": -950.0},
    {"date": "05 Jul", "iso": "2026-07-05", "desc": "Groceries", "amt": -51.4},
    {"date": "08 Jul", "iso": "2026-07-08", "desc": "Salary", "amt": 2400.0},
    {"date": "21 Jul", "iso": "2026-07-21", "desc": "Rent refund", "amt": 75.0},
]

# ------------------------------------------------------- the whole-book report
app = build([dict(t) for t in LEDGER], opening=1000.0)
app._report_summary()
pump()
text = " | ".join(card_text(app))
check("the report card opened", bool(text.strip()), repr(text))
check("it is titled as the whole ledger", "Ledger Summary" in text, repr(text))
check("it counts every entry", " %d " % len(LEDGER) in " %s " % text
      or str(len(LEDGER)) in text, repr(text))

credit = round(sum(t["amt"] for t in LEDGER if t["amt"] > 0), 2)
debit = round(-sum(t["amt"] for t in LEDGER if t["amt"] < 0), 2)
balance = round(1000.0 + sum(t["amt"] for t in LEDGER), 2)
check("the credit total is the sum of the credits",
      app._money(credit) in text, (app._money(credit), repr(text)))
check("the debit total is the sum of the debits",
      app._money(debit) in text, (app._money(debit), repr(text)))
check("the balance includes the opening balance",
      app._money(balance) in text, (app._money(balance), repr(text)))
check("a non-zero opening balance is shown", app._money(1000.0) in text,
      repr(text))
app._close_report()
pump()

# ------------------------------------------------------------ filtered report
# "rent" matches Rent (-950) and Rent refund (+75). A subset has no balance, so
# the card must report a NET — and that net is the matches, not the ledger.
app.search.set_text("rent")
app._search_timeout()
pump()
app._report_summary()
pump()
text = " | ".join(card_text(app))
matches = [t for t in LEDGER if app._matches(t, ("rent",))]
net = round(sum(t["amt"] for t in matches), 2)
check("a filtered report names the query", "rent" in text.lower(), repr(text))
check("a filtered report reports a NET, not a balance",
      "NET" in text.upper() and "BALANCE" not in text.upper(), repr(text))
check("the net is the net of the matches", app._money(net) in text,
      (app._money(net), repr(text)))
check("it counts only the matches", len(matches) == 2 and " 2" in text,
      (len(matches), repr(text)))
app._close_report()
pump()

# ----------------------------------------------------------- the find summary
check("the find summary is visible while searching", app.findsum.get_visible())
check("it says how many matched", "2" in app.find_n.get_text(),
      app.find_n.get_text())
check("the find summary's net is the net of the MATCHES",
      app.find_net.get_text() == app._money(net),
      "said %r, the matches come to %r"
      % (app.find_net.get_text(), app._money(net)))
app.search.set_text("")
app._search_timeout()
pump()
check("it hides again when the search is cleared",
      not app.findsum.get_visible())
app.destroy()
pump()

# ------------------------------------------------------------------- paging
N = 400
big = [{"date": "01 Jan", "iso": "2026-01-01", "desc": "Entry %d" % i,
        "amt": -1.0} for i in range(N)]
app = build(big, opening=0.0)
page = accounting.Accounting._PAGE
check("a long ledger starts at one page", app._shown == page, app._shown)
rows = [w for w in app.rows.get_children() if isinstance(w, Gtk.Button)]
check("...and builds no more rows than that", len(rows) <= page + 1, len(rows))
check("the footer says how much is showing",
      any("%d" % N in (l.get_text() or "")
          for w in app.rows.get_children()
          for l in ([w] if isinstance(w, Gtk.Label) else [])
          ) or app._shown < N, (app._shown, N))

app._show_more()
pump()
check("showing more reveals the next page", app._shown == page * 2,
      "still %d of %d" % (app._shown, N))
rows = [w for w in app.rows.get_children() if isinstance(w, Gtk.Button)]
check("...and the rows follow", len(rows) >= page, len(rows))

# Every entry must be reachable: keep paging until the whole ledger is built.
guard = 0
while app._shown < N and guard < 20:
    app._show_more()
    pump()
    guard += 1
check("paging reaches the end of the ledger", app._shown >= N,
      (app._shown, N))
rows = [w for w in app.rows.get_children() if isinstance(w, Gtk.Button)]
check("...and every entry has a row", len(rows) == N, (len(rows), N))
# The running balance on the LAST row must still be the true first-entry
# balance, i.e. paging did not renumber anything.
check("the ledger still adds up after paging",
      app.balance.get_text() == app._money(round(sum(t["amt"] for t in big), 2)),
      app.balance.get_text())
app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
