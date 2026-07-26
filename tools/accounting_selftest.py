#!/usr/bin/env python3
"""accounting_selftest — prove the ledger is right about money.

    DISPLAY=:0 python3 tools/accounting_selftest.py

This is the app where being wrong costs the user real money, so the two things
that must never regress are checked here rather than eyeballed:

* **The arithmetic.** The table's running balance is accumulated a rounded step
  at a time; the headline BALANCE rounds one raw sum at the end. Those are two
  different calculations of the same number and they have to agree — on a
  hand-picked ledger, on thousands of random cent amounts, and at magnitudes
  where floating point starts to hurt. They only can if every stored amount is
  a whole number of cents, which is what _cents() is for.

* **The data.** Nothing the user committed may be lost by a crash mid-save, and
  a ledger file that comes back damaged must give up only the damaged part —
  not the whole history, and never silently.

Run it after any change to accounting.py. Exit status is the number of failures.
"""
import json
import os
import random
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

HOME = tempfile.mkdtemp(prefix="acct-selftest-")
os.environ["NB_HOME"] = HOME
os.makedirs(os.path.join(HOME, ".config", "notebook"), exist_ok=True)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import accounting                                             # noqa: E402

A = accounting.Accounting
FAILS = []


def check(name, ok, detail=""):
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   " + str(detail)))
    if not ok:
        FAILS.append(name)


def write_ledger(tx, opening=0.0):
    with open(accounting.TX_FILE, "w") as fh:
        json.dump({"tx": tx, "opening": opening}, fh)


def fresh():
    """A ledger app on the current TX_FILE."""
    return A()


# --------------------------------------------------------------- arithmetic
def running_vs_total(tx, opening=0.0):
    """(last running balance, headline balance) for `tx` — the two independent
    routes to the same figure."""
    app = A.__new__(A)
    app.tx = tx
    app.opening = opening
    series = A._balance_series(app)
    total = round(opening + sum(t["amt"] for t in tx), 2)
    return series[-1], total


def tx(*amts):
    return [{"date": "1 Jan", "desc": "x", "amt": accounting._cents(a)}
            for a in amts]


print("== arithmetic ==")

# the classic float trap: a third of a dollar, many times over
r, t = running_vs_total(tx(*([0.1] * 30 + [-0.3] * 10)))
check("0.1 x30 - 0.3 x10 nets to zero", r == t == 0.0, (r, t))

# the drift this app used to have: sub-cent amounts quantise at the door, so
# the two routes cannot diverge
r, t = running_vs_total(tx(*([-0.005] * 7)))
check("sub-cent amounts agree", r == t, (r, t))

# every typed amount is snapped to cents before it is ever stored
for raw, want in (("0.005", 0.01), ("1.004", 1.0), ("12.345", 12.35),
                  ("$1,234.56", 1234.56), ("-50", 50.0), (" 12 ", 12.0),
                  ("1e3", 1000.0)):
    got = A._parse_amount(raw)
    check("parse %r -> %r" % (raw, want), got == want, got)

for raw in ("", "abc", "0", "0.00", "0.004", "1e400", "nan", "inf", "-0"):
    check("reject %r" % raw, A._parse_amount(raw) is None, A._parse_amount(raw))

# "you typed something, it just isn't money" must not read as "type something"
check("0.004 gets the cent message",
      A._missing_msg("Coffee", None, "0.004") == "Enter an amount of at least $0.01",
      A._missing_msg("Coffee", None, "0.004"))
check("empty gets the plain message",
      A._missing_msg("Coffee", None, "") == "Enter an amount",
      A._missing_msg("Coffee", None, ""))

# a big random ledger: the two routes must agree exactly, every time
random.seed(20260725)
bad = 0
for trial in range(40):
    amts = [random.choice((1, -1)) * round(random.uniform(0.01, 9999.99), 2)
            for _ in range(500)]
    r, t = running_vs_total(tx(*amts))
    if r != t:
        bad += 1
check("40 x 500 random cent ledgers agree", bad == 0, "%d disagreed" % bad)

# large magnitudes, where a double starts to lose cents
r, t = running_vs_total(tx(*([99999999.99, -0.01] * 200)))
check("large-magnitude ledger agrees", r == t, (r, t))

# a ledger the user could plausibly type, checked against exact integer cents
amts = [-12.34, 2000.00, -899.99, -0.01, -45.67, 1.5, -1000.0]
r, t = running_vs_total(tx(*amts))
exact = sum(int(round(a * 100)) for a in amts) / 100.0
check("hand ledger matches exact cents", r == t == exact, (r, t, exact))

# display never invents a signed zero, and never prints "nan"
check("-0.001 renders $0.00", A._money(-0.001) == "$0.00", A._money(-0.001))
check("nan renders $0.00", A._money(float("nan")) == "$0.00")
check("negative carries the minus", A._money(-12.5) == "−$12.50",
      A._money(-12.5))
check("thousands separated", A._money(1234567.89) == "$1,234,567.89",
      A._money(1234567.89))

# ------------------------------------------------------------- persistence
print("== persistence ==")

write_ledger([{"date": "1 Jan", "desc": "Rent", "amt": -800.0},
              {"date": "2 Jan", "desc": "Pay", "amt": 1500.0}])
app = fresh()
check("round-trips a saved ledger", len(app.tx) == 2 and app.tx[0]["amt"] == -800.0,
      app.tx)
app.add_entry("Coffee", -3.5)
app2 = fresh()
check("committed entry survives a relaunch",
      len(app2.tx) == 3 and app2.tx[2]["desc"] == "Coffee", app2.tx)

def damaged_copies():
    return sorted(n for n in os.listdir(accounting.CFG_DIR)
                  if n.startswith("accounting.json.damaged-"))


def clear_damaged():
    for n in damaged_copies():
        os.remove(os.path.join(accounting.CFG_DIR, n))


# saving goes through the ONE shared crash-safe writer, not a private copy —
# so every hardening done there (fsync, directory fsync, quarantine, stale-temp
# reaping) applies here too and cannot drift out of step
check("uses the shared atomic writer",
      not hasattr(accounting, "_atomic_write_json"))

# a half-written file must never be what is left on disk: the write goes to a
# temp file and is renamed into place, so an interrupted save leaves the
# PREVIOUS complete ledger, not a truncated one
before = open(accounting.TX_FILE).read()
try:
    import nbapp                                              # noqa: E402
    nbapp.atomic_write_json(accounting.TX_FILE, {"tx": [set()]})
except Exception:
    pass
check("failed save leaves the old file intact",
      open(accounting.TX_FILE).read() == before)
check("failed save leaves no temp files",
      not [n for n in os.listdir(accounting.CFG_DIR) if n.startswith(".nbw-")],
      os.listdir(accounting.CFG_DIR))

# damaged file: a truncated tail must cost the tail, not the history
clear_damaged()
good = json.dumps({"tx": [{"date": "%d Jan" % (i + 1), "desc": "Item %d" % i,
                           "amt": -1.0 * (i + 1)} for i in range(50)],
                   "opening": 0.0})
with open(accounting.TX_FILE, "w") as fh:
    fh.write(good[:len(good) - 40])
app = fresh()
check("truncated file salvages the intact entries",
      48 <= len(app.tx) <= 49, len(app.tx))
check("salvage says so", "Recovered" in app.status_lbl.get_text(),
      app.status_lbl.get_text())
app._autosave()
check("damaged original is preserved when the ledger is written back",
      len(damaged_copies()) == 1, damaged_copies())

# damage in the MIDDLE, not at the end
mid = good[:300] + "@@@@" + good[304:]
with open(accounting.TX_FILE, "w") as fh:
    fh.write(mid)
app = fresh()
check("mid-file damage still salvages most entries", len(app.tx) >= 45,
      len(app.tx))

# hopeless file: open empty, but SAY so, and keep the original
with open(accounting.TX_FILE, "w") as fh:
    fh.write("this is not a ledger at all")
app = fresh()
check("unreadable file opens empty", app.tx == [])
check("unreadable file is announced",
      "could not be read" in app.status_lbl.get_text(),
      app.status_lbl.get_text())
check("unreadable empty state does not say 'add an entry'",
      "could not be read" in app._empty_text(), app._empty_text())

# junk INSIDE a readable file is dropped per-record, not wholesale — and the
# file that still holds the dropped records is kept, not overwritten
clear_damaged()
write_ledger([{"date": "1 Jan", "desc": "Good", "amt": -5.0},
              {"date": "2 Jan", "desc": "NaN", "amt": float("nan")},
              "not a dict",
              {"date": "3 Jan", "desc": "Also good", "amt": 7.0}])
app = fresh()
check("bad records dropped, good records kept",
      [t["desc"] for t in app.tx] == ["Good", "Also good"], app.tx)
check("a partly-bad ledger says how many survived",
      "Recovered 2" in app.status_lbl.get_text(), app.status_lbl.get_text())
app._autosave()
check("a partly-bad ledger is preserved before it is rewritten",
      len(damaged_copies()) == 1, damaged_copies())

# VALID JSON OF THE WRONG SHAPE. It parses, so the generic quarantine in nbapp
# cannot see it; without app-specific handling the ledger opens empty and the
# next save destroys whatever the file really held.
for shape in ('{"tx": "everything"}', '{"entries": [{"amt": -1}]}',
              '"just a string"', '42', '{"tx": {"a": 1}}',
              '{"tx": [1, 2, 3]}', '[{"nope": true}]'):
    clear_damaged()
    with open(accounting.TX_FILE, "w") as fh:
        fh.write(shape)
    app = fresh()
    ok = (app.tx == [] and app._damaged
          and "could not be read" in app.status_lbl.get_text())
    app._autosave()
    check("wrong shape %s is quarantined, not overwritten" % shape[:22],
          ok and len(damaged_copies()) == 1,
          (app.tx, app.status_lbl.get_text(), damaged_copies()))
    if damaged_copies():
        kept = os.path.join(accounting.CFG_DIR, damaged_copies()[0])
        check("  ...and the original bytes are what was kept",
              open(kept).read() == shape, open(kept).read())

# ...but an EMPTY ledger is a legitimate state a user reaches by deleting their
# last entry, and must never be mistaken for damage
clear_damaged()
write_ledger([])
app = fresh()
check("an empty ledger is not treated as damaged",
      app.tx == [] and not app._damaged and app.status_lbl.get_text() == "",
      (app._damaged, app.status_lbl.get_text()))
app._autosave()
check("an empty ledger is not quarantined", damaged_copies() == [],
      damaged_copies())

# a hand-edited file with sub-cent amounts is cleaned on the way in
write_ledger([{"date": "1 Jan", "desc": "x", "amt": -0.005}] * 7)
app = fresh()
r, t = running_vs_total(app.tx, app.opening)
check("loaded amounts are quantised too", r == t, (r, t))

# ------------------------------------------------------------------- search
print("== find ==")

write_ledger([{"date": "3 Mar", "desc": "Food shop", "amt": -21.5},
              {"date": "4 Apr", "desc": "Food shop", "amt": -30.0},
              {"date": "5 Mar", "desc": "Salary", "amt": 2000.0},
              {"date": "6 Mar", "desc": "Bus fare", "amt": -2.4}])
app = fresh()


def find(q):
    app.search.set_text(q)
    app._search_timeout()
    return [t for t in app.tx if app._matches(t, app._terms)]


check("one term matches descriptions", len(find("food")) == 2, find("food"))
check("a month name matches the date", len(find("mar")) == 3, find("mar"))
# the question the search exists to answer, in one query
check("terms are ANDed across fields", len(find("food mar")) == 1,
      find("food mar"))
check("term order does not matter", len(find("mar food")) == 1,
      find("mar food"))
check("case does not matter", len(find("FOOD MAR")) == 1, find("FOOD MAR"))
check("an amount is searchable", len(find("2.40")) == 1, find("2.40"))
check("no match is empty, not everything", find("zzz") == [])
check("empty query shows everything", app._terms == () or find("") == app.tx)
# matching is plain substring, deliberately: "mar" finds "market" as well as
# March. Documented here so it is a decision, not a surprise.
check("substring, not word-boundary",
      A._matches({"date": "4 Apr", "desc": "Supermarket", "amt": -1.0},
                 ("mar",)))

# a new entry must never land where the user cannot see it
app.search.set_text("groceries")
app._search_timeout()
app.f_desc.set_text("Petrol")
app.f_amt.set_text("40")
app._on_add()
check("adding while filtered drops the filter", app._terms == (), app._terms)
check("the added entry is in the ledger",
      app.tx[-1]["desc"] == "Petrol" and app.tx[-1]["amt"] == -40.0, app.tx[-1])

# ------------------------------------------------------------------ export
print("== export ==")

write_ledger([{"date": "1 Jan", "desc": "Rent, monthly", "amt": -800.0},
              {"date": "2 Jan", "desc": 'He said "hi"', "amt": 1500.0}])
app = fresh()
app._export_csv()
import csv                                                    # noqa: E402
csvs = [n for n in os.listdir(accounting.DOCS_DIR) if n.endswith(".csv")]
check("CSV lands in Documents", len(csvs) == 1, csvs)
if csvs:
    with open(os.path.join(accounting.DOCS_DIR, csvs[0]), newline="") as fh:
        rows = list(csv.reader(fh))
    check("CSV has a header and every entry", len(rows) == 3, rows)
    check("CSV quotes commas and quotes in descriptions",
          rows[1][1] == "Rent, monthly" and rows[2][1] == 'He said "hi"', rows)
    check("CSV debit/credit are bare numbers a spreadsheet can add",
          rows[1][2] == "800.00" and rows[1][3] == "" and rows[2][3] == "1500.00",
          rows)
    check("CSV running balance matches the app",
          rows[2][4] == "700.00", rows)

app._export_pdf()
pdfs = [n for n in os.listdir(accounting.DOCS_DIR) if n.endswith(".pdf")]
check("PDF still exports", len(pdfs) == 1, pdfs)

shutil.rmtree(HOME, ignore_errors=True)
print("\n%d check(s) failed" % len(FAILS) if FAILS else "\nall checks passed")
sys.exit(len(FAILS))
