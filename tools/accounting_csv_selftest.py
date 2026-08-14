#!/usr/bin/env python3
"""Self-test for Accounting's spreadsheet-safe CSV export.

Run from the repository root with::

    tools/guestrun.sh python3 tools/accounting_csv_selftest.py

RED PROOFS
    Recorded after deliberate, temporary mutations of accounting.py; the file
    was restored after every run.

    * Mutation: changed ``csv.writer(fh)`` to ``csv.writer(fh,
      quoting=csv.QUOTE_NONE, escapechar="\\")``. Measured failures:
      ``FAIL csv.reader parses equal-width rows   [6, 4, 4, 6, 6, 6, 6,
      6, 6, 6, 6, 6, 6, 6]``; ``FAIL comma quote and newline description
      round-trips   ['2026-08-01', '01 Aug', 'Comma\\',
      ' quote \\" and newline\\']``; ``FAIL debit and credit cells are bare
      float numbers   []``; ``FAIL one data row per transaction   (13, 12)``;
      ``FAIL running balances match app arithmetic to the cent   ([],
      [1222.22, 3222.22, 2322.23, 2322.22, 2276.55, 2278.05, 1278.05,
      1528.3, 1524.97, 1542.86, 1453.98, 1853.98])``; and ``FAIL UTF-8
      non-ASCII description round-trips   ['remain exact', '12.34', '',
      '1222.22']``.

    * Mutation: prefixed both exported amount formats with ``$``. Measured:
      ``FAIL debit and credit cells are bare float numbers   ['$12.34',
      '$2000.00', '$899.99', '$0.01', '$45.67', '$1.50', '$1000.00',
      '$250.25', '$3.33', '$17.89', '$88.88', '$400.00']``.

    * Mutation: replaced the cumulative update with ``bal =
      round(self.opening + t["amt"], 2)``. Measured: ``FAIL running balances
      match app arithmetic to the cent   ([1222.22, 3234.56, 334.57, 1234.55,
      1188.89, 1236.06, 234.56, 1484.81, 1231.23, 1252.45, 1145.68,
      1634.56], [1222.22, 3222.22, 2322.23, 2322.22, 2276.55, 2278.05,
      1278.05, 1528.3, 1524.97, 1542.86, 1453.98, 1853.98])``.
"""
import csv
import json
import os
import shutil
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = "/home/ben/Documents/notebookos-linux/.acct-csv-scratch/nb-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402,F401
import accounting  # noqa: E402


FAILS = []


def check(name, passed, detail=""):
    print("%-4s %s%s" % ("ok" if passed else "FAIL", name,
                         "" if passed else "   " + str(detail)))
    if not passed:
        FAILS.append(name)


def write_store(tx, opening):
    with open(accounting.TX_FILE, "w", encoding="utf-8") as fh:
        json.dump({"opening": opening, "tx": tx}, fh, ensure_ascii=False)


def csv_path():
    names = sorted(n for n in os.listdir(accounting.DOCS_DIR)
                   if n.endswith(".csv"))
    return os.path.join(accounting.DOCS_DIR, names[-1]) if names else None


def read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def fresh_window():
    """Construct the full window when GTK is available; otherwise construct
    its GObject shell and exercise the same app loader/export methods. This
    keeps the self-test runnable on builders with no accessible X display."""
    if Gtk.init_check()[0]:
        return accounting.Accounting()
    win = accounting.Accounting.__new__(accounting.Accounting)
    state = win._load_state()
    win.tx = state["tx"]
    win.opening = state["opening"]
    win._flash = lambda *_args, **_kwargs: None
    return win


opening = 1234.56
special = 'Comma, quote " and newline\nremain exact'
unicode_desc = "房租 / Аренда / 🧾"
amounts = [-12.34, 2000.00, -899.99, -0.01, -45.67, 1.50,
           -1000.00, 250.25, -3.33, 17.89, -88.88, 400.00]
descriptions = [special, unicode_desc] + ["Entry %02d" % i for i in range(3, 13)]
transactions = [
    {"date": "%02d Aug" % (i + 1), "iso": "2026-08-%02d" % (i + 1),
     "desc": descriptions[i], "amt": amount}
    for i, amount in enumerate(amounts)
]

write_store(transactions, opening)
win = fresh_window()
# Fail immediately if the fixture schema is wrong: later export checks would be
# misleading if Accounting quarantined it or skipped its records.
assert len(win.tx) == len(transactions), (len(win.tx), win.tx)
win._export_csv()
path = csv_path()

created = bool(path and os.path.commonpath([os.path.abspath(path),
                                            os.path.abspath(H + "/Documents")])
               == os.path.abspath(H + "/Documents") and os.path.getsize(path) > 0)
check("CSV created non-empty under NB_HOME/Documents", created, path)

rows = read_csv(path) if created else []
same_width = bool(rows) and all(len(row) == len(rows[0]) for row in rows)
check("csv.reader parses equal-width rows", same_width,
      [len(row) for row in rows])

header = rows[0] if rows else []
column = {name: i for i, name in enumerate(header)}
data = rows[1:]
round_trip = ("Description" in column and len(data) >= 1
              and data[0][column["Description"]] == special)
check("comma quote and newline description round-trips", round_trip,
      data[0] if data else rows)

amount_cells = []
if ("Debit" in column and "Credit" in column
        and all(len(row) == len(header) for row in data)):
    amount_cells = [row[column[name]] for row in data
                    for name in ("Debit", "Credit") if row[column[name]]]
forbidden = ("$", ",", "\u2212")
bare = bool(amount_cells) and all(not any(c in value for c in forbidden)
                                  for value in amount_cells)
try:
    for value in amount_cells:
        float(value)
except (TypeError, ValueError):
    bare = False
check("debit and credit cells are bare float numbers", bare, amount_cells)

check("one data row per transaction", len(data) == len(transactions),
      (len(data), len(transactions)))

expected = win._balance_series()[1:]
actual = []
try:
    if not all(len(row) == len(header) for row in data):
        raise ValueError("unequal row widths")
    actual = [float(row[column["Balance"]]) for row in data]
except (IndexError, KeyError, TypeError, ValueError):
    pass
balances_match = (len(actual) == len(expected)
                  and all(round(got, 2) == round(want, 2)
                          for got, want in zip(actual, expected)))
check("running balances match app arithmetic to the cent", balances_match,
      (actual, expected))

unicode_ok = ("Description" in column and len(data) >= 2
              and len(data[1]) > column["Description"]
              and data[1][column["Description"]] == unicode_desc)
check("UTF-8 non-ASCII description round-trips", unicode_ok,
      data[1] if len(data) >= 2 else rows)

write_store([], 17.25)
empty_win = fresh_window()
assert len(empty_win.tx) == 0, empty_win.tx
empty_error = None
try:
    empty_win._export_csv()
    empty_rows = read_csv(csv_path())
except Exception as exc:
    empty_error = repr(exc)
    empty_rows = []
check("empty ledger exports header and no data", len(empty_rows) == 1,
      empty_error or empty_rows)

shutil.rmtree(H, ignore_errors=True)
if FAILS:
    print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("\nall checks passed")
sys.exit(0)
