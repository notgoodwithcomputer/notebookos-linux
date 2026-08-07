#!/usr/bin/env python3
"""
A ledger you can reconcile.

ROADMAP #7: entries stored `{"date": "28 Jul"}` — no year — and the CSV wrote
that string straight out, so an exported ledger crossing a year boundary could
not be sorted or reconciled. Twelve months of "6 Aug" and "6 Aug" are the same
cell to a spreadsheet.

WHY THE DISPLAY DID NOT CHANGE. Putting the year in the shown string was the
obvious move and is wrong: measured in the report face at 9.5pt, "26 Sep 2026"
is **61pt** against the **58pt** the PDF ledger gives the date column, so it
would run into DESCRIPTION on every row. New entries therefore carry a separate
`iso` field — the date a spreadsheet can use — beside the short display string,
and the CSV leads with it.

WHAT MIGRATION MEANS HERE. Nothing is rewritten. An entry saved before this
existed keeps its "28 Jul" and gets NO `iso`: a year cannot be inferred from a
row that never recorded one, and a ledger is the last place to invent data. Its
CSV cell is left EMPTY, which a person can see and fix, rather than filled with
a plausible guess they cannot.

Run:
    tools/guestrun.sh python3 tools/accounting_dates_selftest.py
    tools/guestrun.sh python3 tools/accounting_dates_selftest.py --de DIR
"""
import os
import re
import sys
import csv
import json
import time
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-acct-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import accounting  # noqa: E402

FAILED, N = [], [0]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(300):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def main():
    app = accounting.Accounting()
    pump()

    # ---- a new entry records a date a machine can read ---------------
    app.f_desc.set_text("Bread")
    app.f_amt.set_text("3.20")
    app.fdir = "debit"
    app._on_add()
    pump()
    added = check("an entry is committed", len(app.tx) == 1)
    if not added:
        not_reached("nothing was added", "it carries an ISO date",
                    "the ISO date is today", "the shown date is unchanged")
        return 1
    t = app.tx[0]
    got = check("it carries an ISO date", ISO.match(t.get("iso", "") or "") is not None,
                repr(t.get("iso")))
    if got:
        check("the ISO date is today", t["iso"] == time.strftime("%Y-%m-%d"),
              t["iso"])
    else:
        not_reached("no ISO date", "the ISO date is today")
    # The display column is 58pt in the PDF and cannot hold a year; the short
    # form is what ships and must be left alone.
    check("the shown date is still the short form",
          re.match(r"^\d{1,2} [A-Za-z]{3}$", t.get("date", "")) is not None,
          repr(t.get("date")))

    # ---- an OLD entry is not rewritten -------------------------------
    # Exactly what a ledger written before this change looks like.
    app.tx.insert(0, {"date": "28 Jul", "desc": "Old coffee", "amt": -2.5})
    app._autosave()
    pump()
    on_disk = json.load(open(os.path.join(
        _HOME, ".config", "notebook", "accounting.json")))
    old = [r for r in on_disk["tx"] if r.get("desc") == "Old coffee"]
    kept = check("the old entry survives a save", len(old) == 1)
    if kept:
        check("...and no year was invented for it", "iso" not in old[0],
              repr(old[0]))
        check("...and its shown date is untouched", old[0]["date"] == "28 Jul")
    else:
        not_reached("the old entry vanished", "...and no year was invented for it",
                    "...and its shown date is untouched")

    # ---- and it survives a RELOAD, which is where a loader drops things
    app2 = accounting.Accounting()
    pump()
    descs = [r.get("desc") for r in app2.tx]
    check("both entries reload", "Old coffee" in descs and "Bread" in descs,
          repr(descs))
    new_r = [r for r in app2.tx if r.get("desc") == "Bread"]
    old_r = [r for r in app2.tx if r.get("desc") == "Old coffee"]
    if new_r and old_r:
        check("the loader keeps the ISO date it was given",
              ISO.match(new_r[0].get("iso", "") or "") is not None,
              repr(new_r[0].get("iso")))
        check("the loader invents none for the entry without one",
              "iso" not in old_r[0], repr(old_r[0]))
    else:
        not_reached("an entry did not reload",
                    "the loader keeps the ISO date it was given",
                    "the loader invents none for the entry without one")

    # ---- editing must not cost the entry its date --------------------
    # _save_edit REBUILDS the dict, so a field not named there is dropped.
    idx = [i for i, r in enumerate(app2.tx) if r.get("desc") == "Bread"][0]
    app2._edit_idx = idx
    app2._edit_tx(idx)
    pump()
    if hasattr(app2, "_e_desc") and app2._e_desc is not None:
        app2._e_desc.set_text("Bread and jam")
        app2._e_amt.set_text("4.10")
        app2._e_date.set_text("6 Aug")
        app2._save_edit()
        pump()
        edited = [r for r in app2.tx if r.get("desc") == "Bread and jam"]
        did = check("the edit lands", len(edited) == 1)
        if did:
            check("editing does not drop the ISO date",
                  ISO.match(edited[0].get("iso", "") or "") is not None,
                  repr(edited[0]))
        else:
            not_reached("the edit did not land",
                        "editing does not drop the ISO date")

    # ---- the CSV leads with a sortable column ------------------------
    docs = os.path.join(_HOME, "Documents")
    os.makedirs(docs, exist_ok=True)
    app2._export_csv()
    pump()
    csvs = [f for f in os.listdir(docs) if f.endswith(".csv")]
    wrote = check("a CSV is exported", bool(csvs), repr(csvs))
    if not wrote:
        not_reached("no CSV", "the first column is the sortable date",
                    "the entry with no year leaves that cell empty")
        return 1
    with open(os.path.join(docs, csvs[0])) as fh:
        rows = list(csv.reader(fh))
    head = rows[0] if rows else []
    check("the first column is the sortable date (%s)" % head[:2],
          head and head[0] == "Date" and len(head) >= 6)
    body = rows[1:]
    isos = [r[0] for r in body if len(r) > 1]
    check("the new entry exports an ISO date",
          any(ISO.match(v or "") for v in isos), repr(isos))
    check("the entry with no year leaves that cell empty, not guessed",
          any(v == "" for v in isos), repr(isos))
    check("every row still carries its shown date",
          all(len(r) > 1 and r[1] for r in body), repr([r[:2] for r in body]))

    try:
        app.destroy()
        app2.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
