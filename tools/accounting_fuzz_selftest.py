#!/usr/bin/env python3
"""Every malformed ledger file I could think of, opened twice.

This app's most safety-critical path is the one that runs when the file is not
what it expects, and the failure mode that matters is not a crash — it is the
QUIET one: the app opens on a damaged store, shows a short or empty ledger, and
the close-time save writes that over the only copy. That has happened here
before, which is why `_load_state` quarantines, `_salvage_tx` walks broken text
for intact entries, and `_save_to_disk` refuses to write an empty book over a
non-empty file.

So each case is opened, closed, and OPENED AGAIN, and the second count must not
be lower than the first. One open proves it survives; two prove it does not eat
itself.

WHAT IS NOT ASSERTED: the exact entry count for a given corruption. That is a
judgement the salvage makes case by case and pinning it would make this file a
change-detector. But a FLOOR is asserted for the cases where the data is
essentially intact and only the wrapper is damaged — trailing or leading
garbage, a BOM, CRLF, a truncated tail. Recovering something there is the whole
point of `_salvage_tx`.

That floor is not decoration, it is the correction of a real hole in the first
version of this file. Comparing only "first open vs second open" cannot see the
salvage getting WORSE: gut `_salvage_tx` so it returns nothing and both opens
return zero, the delta is still zero, and the suite stayed green against a
mutation that destroys the entire recovery path. A check that measures a
difference is blind to anything that moves both sides.

RED PROOFS (M1), measured:

  1. the empty-model guard is removed from `_save_to_disk`
     (the `os.path.getsize(...) > 2` refusal)
       LOSS  truncated mid-entry: 0 -> 0 on the second open ... and the file it
       could have recovered from is gone. Several cases lose their originals.
  2. `_salvage_tx` returns nothing (`return out` -> `return []`)
       LOSS  truncated at the end: 1 -> 0
       LOSS  trailing garbage: 2 -> 0
       LOSS  leading garbage: 2 -> 0, and the rest of the salvage family
"""

import os, sys, shutil, json, traceback

BASE = "/tmp/nbhome-acctfuzz-%d" % os.getpid()
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

GOOD = {"opening": 100.0,
        "tx": [{"date": "01 Aug", "iso": "2026-08-01", "desc": "Rent",
                "amt": -950.0},
               {"date": "02 Aug", "iso": "2026-08-02", "desc": "Salary",
                "amt": 2400.0}]}

CASES = {
    "empty file": "",
    "whitespace only": "   \n  ",
    "a bare number": "42",
    "a bare string": '"hello"',
    "a JSON null": "null",
    "an array of numbers": "[1,2,3]",
    "tx is a string": '{"tx": "everything"}',
    "tx is a number": '{"tx": 5}',
    "tx holds nulls": '{"tx": [null, null]}',
    "tx holds arrays": '{"tx": [[1,2],[3,4]]}',
    "opening is a string": '{"opening": "lots", "tx": []}',
    "opening is null": '{"opening": null, "tx": []}',
    "opening is a list": '{"opening": [1], "tx": []}',
    "amt is a string": '{"tx":[{"desc":"x","amt":"12.34"}]}',
    "amt is null": '{"tx":[{"desc":"x","amt":null}]}',
    "amt is a dict": '{"tx":[{"desc":"x","amt":{"a":1}}]}',
    "amt missing": '{"tx":[{"desc":"x"}]}',
    "desc is a number": '{"tx":[{"desc":42,"amt":-1.0}]}',
    "desc is a list": '{"tx":[{"desc":["a"],"amt":-1.0}]}',
    "date is a dict": '{"tx":[{"date":{"y":1},"desc":"x","amt":-1.0}]}',
    "iso is garbage": '{"tx":[{"iso":"not-a-date","desc":"x","amt":-1.0}]}',
    "deeply nested": '{"tx":[{"desc":"x","amt":-1.0,"deep":' + '{"a":' * 40 + '1' + '}' * 40 + '}]}',
    "duplicate keys": '{"tx":[],"tx":[{"desc":"x","amt":-1.0}]}',
    "unicode everywhere": json.dumps({"opening": 1.0, "tx": [
        {"date": "١ أغسطس", "desc": "房租 🏠 Аренда", "amt": -1.0}]}),
    "a huge description": json.dumps({"tx": [
        {"desc": "x" * 100000, "amt": -1.0}]}),
    "very many entries": json.dumps({"tx": [
        {"desc": "e%d" % i, "amt": -0.01} for i in range(2000)]}),
    "truncated mid-entry": json.dumps(GOOD)[:60],
    "truncated at the end": json.dumps(GOOD)[:-3],
    "trailing garbage": json.dumps(GOOD) + "@@@@garbage",
    "leading garbage": "@@@@" + json.dumps(GOOD),
    "NUL bytes": json.dumps(GOOD).replace("Rent", "Re\x00nt"),
    "BOM prefixed": "﻿" + json.dumps(GOOD),
    "CRLF everywhere": json.dumps(GOOD).replace(",", ",\r\n"),
}

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
import uishot, accounting  # noqa: E402
uishot.load_theme()


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


# Cases where the ledger content is intact and only the wrapper is damaged.
# The salvage must get SOMETHING back from each of these.
FLOOR = {"trailing garbage": 1, "leading garbage": 1, "BOM prefixed": 1,
         "CRLF everywhere": 1, "truncated at the end": 1, "NUL bytes": 1}

crashes, losses, thin = [], [], []
print("%-24s %-7s %-9s %s" % ("case", "opens", "entries", "note"))
for name, text in CASES.items():
    H = "%s-%d" % (BASE, abs(hash(name)) % 100000)
    shutil.rmtree(H, ignore_errors=True)
    os.makedirs(H + "/.config/notebook", exist_ok=True)
    os.environ["NB_HOME"] = H
    store = H + "/.config/notebook/accounting.json"
    with open(store, "w", encoding="utf-8", errors="surrogatepass") as f:
        f.write(text)
    # The module resolves its paths at IMPORT from NB_HOME, so a new home has
    # to be pushed into it per case; re-importing instead would re-run the
    # single-instance claim and silently exit the loser (see the header of
    # academics_class_selftest).
    accounting.HOME = H
    accounting.CFG_DIR = H + "/.config/notebook"
    accounting.TX_FILE = store
    accounting.DOCS_DIR = H + "/Documents"
    try:
        a = accounting.Accounting()
        pump()
        n = len(a.tx)
        note = (a.status_lbl.get_text() or "")[:38]
        # open + close, twice: the shape that has destroyed data before
        a._on_destroy(); a.destroy(); pump()
        b = accounting.Accounting(); pump()
        n2 = len(b.tx)
        b._on_destroy(); b.destroy(); pump()
        if n2 < n:
            losses.append("%s: %d -> %d on the second open" % (name, n, n2))
        want = FLOOR.get(name)
        if want is not None and n < want:
            thin.append("%s: salvaged %d, expected at least %d"
                        % (name, n, want))
        print("%-24s %-7s %-9s %s" % (name[:24], "yes",
                                      "%d/%d" % (n, n2), note))
    except Exception as exc:
        crashes.append("%s: %s" % (name, exc))
        print("%-24s %-7s %-9s %s" % (name[:24], "CRASH", "-",
                                      type(exc).__name__))
        traceback.print_exc(limit=1)

print()
for c in crashes:
    print("CRASH", c)
for l in losses:
    print("LOSS ", l)
for t in thin:
    print("THIN ", t)
bad = len(crashes) + len(losses) + len(thin)
print("%d cases, %d crash(es), %d loss(es), %d under-salvaged"
      % (len(CASES), len(crashes), len(losses), len(thin)))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
