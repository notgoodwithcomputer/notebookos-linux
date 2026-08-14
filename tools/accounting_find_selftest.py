#!/usr/bin/env python3
"""What the ledger's Find box can and cannot find.

`_matches` ANDs every term across an entry's description, its date and its
figure — which is what makes "what did I spend on food in March?" a single
query, `food mar`. The figure half is the interesting one, because the number a
person types is copied off something: a bank statement, a receipt, or this
app's own screen.

THE DEFECT THIS EXISTS FOR: only `abs(amt)` was in the haystack. So "212.40"
found the entry and "-212.40" found NOTHING — and a debit copied off a statement
carries its minus about as often as not. Worse, the ledger DISPLAYS a typographic
minus (U+2212, `accounting.MINUS`), so a figure copied out of this app's own
column could never match itself.

Both forms are now indexed and the term's minus is normalised, so:
  * "3.50"  finds money in or out, as before
  * "-3.50" finds only money that went out
  * "−3.50" (the app's own minus) does the same

RED PROOF (M1): this suite was written BEFORE the fix and run against the
unmodified app, which is the cleanest form of the proof — no mutation needed,
because the broken behaviour was the shipped one:

    FAIL a debit is found by its signed figure          <- '-3.50' matched 0 of 1
    FAIL the app's own typographic minus finds it too   <- '−3.50' matched 0 of 1
    FAIL a credit is NOT found by a negative figure     (passed; vacuously)
"""
import os
import sys
import shutil

H = "/tmp/nbhome-acctfind-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

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


LEDGER = [
    {"desc": "Coffee", "date": "04 Aug", "iso": "2026-08-04", "amt": -3.50},
    {"desc": "Salary", "date": "01 Aug", "iso": "2026-08-01", "amt": 2400.00},
    {"desc": "Groceries, market stall", "date": "28 Jul",
     "iso": "2026-07-28", "amt": -51.40},
    {"desc": "Refund for the bookshop", "date": "18 Jul",
     "iso": "2026-07-18", "amt": 3.50},
]


def found(query):
    terms = tuple(query.lower().split())
    return [t["desc"] for t in LEDGER
            if accounting.Accounting._matches(t, terms)]


# ------------------------------------------------------------ the text half
check("a description word finds its entry", found("coffee") == ["Coffee"],
      found("coffee"))
check("search is case-insensitive", found("SALARY") == ["Salary"],
      found("SALARY"))
check("a month abbreviation finds that month's entries",
      sorted(found("jul")) == ["Groceries, market stall",
                               "Refund for the bookshop"], found("jul"))
check("terms are ANDed, not ORed", found("groceries salary") == [],
      found("groceries salary"))
check("an empty query matches everything", len(found("")) == len(LEDGER),
      found(""))

# ----------------------------------------------------------- the figure half
check("an unsigned figure finds both directions",
      sorted(found("3.50")) == ["Coffee", "Refund for the bookshop"],
      found("3.50"))
check("a debit is found by its signed figure", found("-3.50") == ["Coffee"],
      "'-3.50' matched %d of 1" % len(found("-3.50")))
check("the app's own typographic minus finds it too",
      found(accounting.MINUS + "3.50") == ["Coffee"],
      "%r matched %d of 1" % (accounting.MINUS + "3.50",
                              len(found(accounting.MINUS + "3.50"))))
# The other direction has to stay honest: a negative query must not drag in the
# credit of the same magnitude, or the sign has been indexed without meaning.
check("a credit is NOT found by a negative figure",
      "Refund for the bookshop" not in found("-3.50"), found("-3.50"))
check("a larger figure is not matched by a smaller one",
      found("51.40") == ["Groceries, market stall"], found("51.40"))

# ---------------------------------------------------------- combined queries
check("a word and a figure together narrow to one",
      found("groceries 51.40") == ["Groceries, market stall"],
      found("groceries 51.40"))
check("a word and a month together narrow",
      found("refund jul") == ["Refund for the bookshop"], found("refund jul"))

# ------------------------------------------------------------- bad input
check("a nonsense query matches nothing", found("zzzzz") == [], found("zzzzz"))
check("a malformed entry does not break the search",
      isinstance(accounting.Accounting._matches(
          {"desc": "x", "date": "y", "amt": None}, ("x",)), bool))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
