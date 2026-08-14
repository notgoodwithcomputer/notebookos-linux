#!/usr/bin/env python3
"""Salvaging a damaged ledger: keep everything real, invent nothing.

    tools/guestrun.sh python3 tools/accounting_salvage_selftest.py

`_salvage_tx`, `_salvage_opening` and `_num` decide how much of somebody's
financial history survives a half-written file, and no suite had ever named
them. The ledger is one `json.dump` line, so the realistic damage — a write cut
short by failing media, a file half-copied off a USB stick — leaves a document
`json.load` rejects outright. Giving up there loses a whole history to one bad
byte, so the recovery walks the raw text and cuts out every balanced `{...}` run
instead.

THAT SCAN RUNS OVER THE USER'S OWN WORDS. A description is the one part of the
file the person chose, and it can contain anything: a brace, a quote, a
backslash, or — the case that matters — a complete fake transaction. The scanner
is string-aware so that cannot confuse it. This file tries to confuse it.

TWO FAILURES, NOT ONE. Losing an entry is the obvious one. INVENTING an entry,
or recovering one TWICE, is just as bad in a ledger and much harder to notice:
a balance that is wrong in the app's favour looks like a balance. Every check
here compares what came back against the entries that were really in the file,
by value, rather than counting them.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the scan stops tracking strings (`if ch == '"': in_str = True` ->
     `if ch == '"': pass`), so braces inside a description are counted
                                                              3 FAILED
       FAIL ...and the entries around it survive
            <- kept 1 of 2 recoverable entries      (x3: the brace, the
               nested braces, and the quote-then-brace descriptions)

     WHAT THIS MUTATION DOES NOT TURN RED, and why that is not a hole: the
     "invents nothing" checks stay green. With string tracking off the scanner
     does see the braces inside a description, but in the RAW text those inner
     quotes are backslash-escaped, so the substring it cuts out is not valid
     JSON and `json.loads` rejects it. The failure mode of losing the scan's
     string-awareness is therefore LOST entries, not invented ones. The
     invention checks are still the right checks — mutation 3 is what turns
     them red — they are simply not what this mutation breaks.
  2. the scan stops honouring escapes (`elif ch == "\\\\": esc = True` ->
     `elif ch == "\\\\": pass`; matches twice, in `_salvage_tx` and
     `_salvage_opening`, and both are mutated)
       FAIL ...and the entries around it survive
            <- kept 1 of 2 recoverable entries
  3. salvage returns entries with no `amt` filter
     (`if isinstance(obj, dict) and "amt" in obj` -> `if isinstance(obj, dict)`)
                                                              2 FAILED
       FAIL an undamaged ledger salvages to exactly its own entries  <- [None]
       FAIL salvage returns only real transactions
            <- salvage returned an entry that was never in the file: [None]

MEASURED VERDICT ON THE CODE ITSELF: none of the ten hostile descriptions
confuses the scanner — including one holding a complete fake transaction with
its own `amt`. Nothing invented, nothing duplicated, and the entries either side
survive. This suite pins defence that was already there; it did not find a
defect. That is worth saying plainly, because a suite arriving with no defect
attached usually means the tests are too weak, and here the red proofs are the
evidence that it is not.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctsalv-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("ACCOUNTING_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import accounting                                             # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def key(t):
    return json.dumps(t, sort_keys=True)


def E(desc, amt, day):
    return {"date": "%02d Aug" % day, "iso": "2026-08-%02d" % day,
            "desc": desc, "amt": amt}


def salvaged(entries, cut=3):
    """Write a ledger, chop `cut` bytes off the end (a write that stopped), and
    return what salvage recovers."""
    doc = {"opening": 0.0, "tx": entries}
    return doc, accounting._salvage_tx(json.dumps(doc)[:-cut] if cut
                                       else json.dumps(doc))


# ------------------------------------------------- descriptions chosen to break it
NASTY = (
    ("a brace in the words", "Rent for {the annex}"),
    ("an unbalanced open brace", "Deposit { unmatched"),
    ("an unbalanced close brace", "Deposit } unmatched"),
    ("an escaped quote", 'The "good" chair'),
    ("a description ending in a backslash", "Path C:\\"),
    ("a whole fake transaction", '{"amt": 999999, "desc": "INVENTED"}'),
    ("nested braces", "{{{deep}}}"),
    ("a quote then a brace", '" } { "'),
    ("a newline", "line one\nline two"),
    ("a full-width brace lookalike", "Rent \uff5bfullwidth\uff5d"),
)

for name, desc in NASTY:
    doc, got = salvaged([E("BEFORE", -1.0, 1), E(desc, -2.0, 2),
                         E("AFTER", -3.0, 3)])
    real = set(key(t) for t in doc["tx"])
    invented = [t for t in got if key(t) not in real]
    check("a description holding %s invents nothing" % name, not invented,
          "salvage returned an entry that was never in the file: %r"
          % [t.get("desc") for t in invented][:3])
    check("...and the entries around it survive" if desc else "",
          sum(1 for t in got if t.get("desc") in ("BEFORE", desc)) == 2,
          "kept %d of 2 recoverable entries"
          % sum(1 for t in got if t.get("desc") in ("BEFORE", desc)))
    check("...and nothing is recovered twice",
          len(got) == len(set(key(t) for t in got)),
          [t.get("desc") for t in got])

# ------------------------------------------------------ an intact file is exact
doc, got = salvaged([E("Rent", -950.0, 1), E("Salary", 2400.0, 2)], cut=0)
check("an undamaged ledger salvages to exactly its own entries",
      [key(t) for t in got] == [key(t) for t in doc["tx"]],
      [t.get("desc") for t in got])
check("salvage returns only real transactions",
      all(t.get("amt") is not None for t in got),
      "salvage returned an entry that was never in the file: %r"
      % [t.get("amt") for t in got])

# ------------------------------------------------------------ the opening balance
for text, want in (('{"opening": 2400.0, "tx": [', 2400.0),
                   ('{"tx": [{"desc": "opening", "amt": -1.0}], "opening": 12',
                    12.0),
                   ('{"opening": -500.5, "tx": [', -500.5),
                   ('{"tx": []', None),
                   ('{"desc": "the opening night", "amt": -3.0}', None)):
    got = accounting._salvage_opening(text)
    check("the opening balance is recovered as %r" % (want,), got == want,
          "got %r from %r" % (got, text[:46]))

# A DESCRIPTION mentioning "opening" must not be mistaken for the field. That is
# the whole reason the scan is depth- and string-aware rather than a regex.
got = accounting._salvage_opening(
    '{"tx": [{"desc": "\\"opening\\": 999", "amt": -1.0}], "opening": 7')
check("a description quoting the opening key does not become the balance",
      got == 7.0, got)

# --------------------------------------------------------------------- _num
# A numeric STRING coerces rather than falling back, and that is deliberate: on
# a salvage path a hand-edited or foreign-written file carrying "amt": "12.50"
# should give back 12.50, not zero. (This suite first asserted the opposite and
# was wrong — the code is recovering data here, not validating typed input,
# which is `_parse_amount`'s job and is strict.)
for raw, want in ((1.5, 1.5), (2, 2.0), ("3.5", 3.5), (" 4.25 ", 4.25),
                  (None, 0.0), (True, 0.0), (False, 0.0), ([1], 0.0),
                  ({"a": 1}, 0.0), ("", 0.0), ("twelve", 0.0),
                  (float("nan"), 0.0), (float("inf"), 0.0),
                  (10 ** 400, 0.0)):
    got = accounting.Accounting._num(raw, 0.0)
    check("_num(%r) is %r" % (raw, want), got == want, got)

# A bool is an int in Python, and "True dollars" is not a figure. Checked apart
# from the table above because it is the one that looks like it should pass.
check("a boolean amount is not read as 1.00",
      accounting.Accounting._num(True, 0.0) == 0.0,
      accounting.Accounting._num(True, 0.0))

# ----------------------------------------------------------- the recovered note
check("the note for one entry is a whole sentence, not a suffixed plural",
      "1 entry" in accounting._recovered_note(1)
      and "entries" not in accounting._recovered_note(1),
      accounting._recovered_note(1))
check("...and the note for two says two",
      "2" in accounting._recovered_note(2), accounting._recovered_note(2))

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
