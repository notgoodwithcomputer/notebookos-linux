#!/usr/bin/env python3
"""
Headless selftest for the Bill Tracker (bills.py) and its desktop tile.

A bill tracker that is wrong about a date or an amount is worse than no bill
tracker at all: it is a machine that confidently tells its owner they have a
week left when the cheque needed posting yesterday. So this file is weighted
towards the arithmetic and the store, not the widgets.

  1. Dates      — the month walk, with the day of the month CLAMPED into short
                  months, leap years and year ends. Checked as an INVARIANT
                  (every occurrence lands on the anchor's day, or on the last
                  day of a month too short to hold it) rather than against a
                  hand-written list of answers, because a list of answers is
                  exactly what a wrong implementation gets written to match.
  2. Money      — whole cents in and out, off what a person types off a paper
                  bill. Never floats: 0.1 + 0.2 is not 0.3 and this is money.
  3. What is due — the outstanding occurrence, the POST BY deadline that comes
                  before it, and the ladder of states. Recording a payment must
                  move the bill on by itself.
  4. The store  — every field re-validated; a section of the wrong type costs
                  only itself; a store this app cannot recognise is moved
                  aside rather than replaced by an empty one.
  5. Round trip — a bill added in the app is on disk after the window closes,
                  and comes back when it opens again.
  6. The tile   — the desktop reads the same file through the same functions
                  and says the same thing the app does.
  7. Language   — a date is an ORDER, not just a word, and Japanese, Spanish
                  and Russian each write one differently.

Run as:
  DISPLAY=:0 \\
  PYTHONPATH=<overlay>/opt/notebook/de \\
  python3 tools/bills_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)

HOME = tempfile.mkdtemp(prefix="nbhome-bills-")
os.environ["NB_HOME"] = HOME
CFG = os.path.join(HOME, ".config", "notebook")
os.makedirs(CFG, exist_ok=True)

import gi                                                       # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                   # noqa: E402,F401

import bills                                                    # noqa: E402
import nbapp                                                    # noqa: E402

FAILED = []


def check(what, cond, detail=None):
    print("%-72s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)
        if detail is not None:
            print("      %r" % (detail,))


def put(name, blob):
    path = os.path.join(CFG, name)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(blob, str):
            fh.write(blob)
        else:
            json.dump(blob, fh)
    return path


def bill(**kw):
    """A normalised bill, so a test never hand-builds a shape the app would
    never have written."""
    base = {"payee": "Someone", "account": "", "amount": 1000,
            "due": "2026-08-15", "every": 1, "method": "mail",
            "address": "", "phone": "", "note": "", "lead": 0, "paid": []}
    base.update(kw)
    return bills.normalise(base)


# -- 1. dates ----------------------------------------------------------------
print("\n-- dates --")

check("a month step keeps the day of the month",
      bills.add_months("2026-08-15", 1) == "2026-09-15",
      bills.add_months("2026-08-15", 1))
check("...and rolls the year over",
      bills.add_months("2026-11-15", 3) == "2027-02-15",
      bills.add_months("2026-11-15", 3))
check("the 31st becomes the 28th in a short February",
      bills.add_months("2026-01-31", 1) == "2026-02-28",
      bills.add_months("2026-01-31", 1))
check("...the 29th in a leap one",
      bills.add_months("2024-01-31", 1) == "2024-02-29",
      bills.add_months("2024-01-31", 1))
check("...and 1900 is NOT a leap year",
      bills.add_months("1900-01-31", 1) == "1900-02-28",
      bills.add_months("1900-01-31", 1))
# THE ONE THAT MATTERS. Clamping the previous RESULT instead of the anchor pins
# a bill due on the 31st to the 28th for the rest of its life after one
# February, which is a wrong date every month thereafter and no error anywhere.
check("...and March gets the 31st back, not February's 28th",
      bills.add_months("2026-01-31", 2) == "2026-03-31",
      bills.add_months("2026-01-31", 2))
check("an impossible date is refused rather than rolled forward",
      bills.add_months("2026-02-31", 1) is None
      and bills.fmt_date("2026-02-31") == "",
      bills.add_months("2026-02-31", 1))

# The invariant, over four years of every day-of-month a bill can carry.
bad = []
for anchor_day in (1, 15, 28, 29, 30, 31):
    b = bill(due="2024-01-%02d" % anchor_day, every=1)
    for i, day in enumerate(bills.occurrences(b, limit=48)):
        y, m, d = (int(p) for p in day.split("-"))
        want = min(anchor_day, bills._month_len(y, m))
        if d != want:
            bad.append((anchor_day, day))
        if i and bills.day_of(day) <= bills.day_of(prev):
            bad.append(("not ascending", day))
        prev = day
check("every occurrence lands on the anchor's day, or the month's last",
      not bad, bad[:4])

check("a one-off has exactly one occurrence",
      len(list(bills.occurrences(bill(every=0)))) == 1)
check("a quarterly bill steps three months at a time",
      list(bills.occurrences(bill(due="2026-01-10", every=3)))[:3]
      == ["2026-01-10", "2026-04-10", "2026-07-10"])
check("a walk over occurrences always terminates",
      len(list(bills.occurrences(bill(due="1200-01-01", every=1)))) <= 2000)


# -- 2. money ----------------------------------------------------------------
print("\n-- money --")

for text, want in (("84.20", 8420), ("$84.20", 8420), ("84", 8400),
                   ("1,204.50", 120450), ("0.05", 5), (".5", 50),
                   ("8.4", 840), ("3.999", 400), ("-12.34", -1234),
                   ("", None), ("  ", None), ("abc", None), (".", None)):
    got = bills.parse_money(text)
    check("%-10r reads as %s" % (text, want), got == want, got)

check("a cent amount prints with both its decimals",
      bills.money(8420) == "$84.20" and bills.money(5) == "$0.05",
      (bills.money(8420), bills.money(5)))
check("...with thousands separated", bills.money(118000) == "$1,180.00",
      bills.money(118000))
check("...and a real minus, not a hyphen",
      bills.money(-1234).startswith("−"), bills.money(-1234))
check("zero is $0.00, never -$0.00", bills.money(0) == "$0.00", bills.money(0))
check("what is typed comes back out unchanged",
      all(bills.money(bills.parse_money(t)) == t.replace(",", "").rjust(0)
          or bills.parse_money(bills.money(bills.parse_money(t)))
          == bills.parse_money(t)
          for t in ("84.20", "1,204.50", "0.05", "118000")))
check("a nonsense amount does not raise",
      bills.money("nope") == "$0.00" and bills.money(None) == "$0.00")


# -- 3. what is due ----------------------------------------------------------
print("\n-- what is due --")

TODAY = "2026-08-05"


def info(**kw):
    return bills.due_info(bill(**kw), TODAY)


check("a bill due today says so", info(due=TODAY)["kind"] == "today",
      info(due=TODAY))
check("a bill past its date is overdue",
      info(due="2026-08-01")["kind"] == "overdue"
      and info(due="2026-08-01")["days"] == -4, info(due="2026-08-01"))
check("a bill three days out is due soon",
      info(due="2026-08-08")["kind"] == "soon", info(due="2026-08-08"))
check("a bill three weeks out is not",
      info(due="2026-08-26")["kind"] == "later", info(due="2026-08-26"))

# The postal deadline: the whole reason this app exists.
posted = info(due="2026-08-20", method="mail", lead=5)
check("a posted bill carries a POST BY date, lead days before it is due",
      posted["post_by"] == "2026-08-15", posted)
check("...and it is exactly `lead` days earlier",
      bills.day_of(posted["due"]) - bills.day_of(posted["post_by"]) == 5)
check("posting is called for when the postal deadline arrives",
      info(due="2026-08-10", method="mail", lead=5)["kind"] == "post",
      info(due="2026-08-10", method="mail", lead=5))
check("...and it OUTRANKS the due date, which is still a week off",
      info(due="2026-08-12", method="mail", lead=7)["kind"] == "post",
      info(due="2026-08-12", method="mail", lead=7))
check("a bill paid over the phone has no postal deadline",
      info(due="2026-08-10", method="phone", lead=5)["post_by"] is None)
check("...nor has one posted with no days allowed",
      info(due="2026-08-10", method="mail", lead=0)["post_by"] is None)
check("a postal deadline that has passed still says POST, not OVERDUE",
      info(due="2026-08-08", method="mail", lead=5)["kind"] == "post")
check("...until the due date itself goes by",
      info(due="2026-08-04", method="mail", lead=5)["kind"] == "overdue")

# What is outstanding, and what recording a payment does to it.
b = bill(due="2026-06-15", every=1,
         paid=[{"on": "2026-06-12", "for": "2026-06-15", "amount": 1000}])
check("a paid occurrence is not the one outstanding",
      bills.due_info(b, TODAY)["due"] == "2026-07-15",
      bills.due_info(b, TODAY))
b["paid"].append({"on": "2026-07-12", "for": "2026-07-15", "amount": 1000})
check("...and each payment moves the bill on by one",
      bills.due_info(b, TODAY)["due"] == "2026-08-15",
      bills.due_info(b, TODAY))
check("a missed month stays outstanding while a later one is paid",
      bills.due_info(bill(due="2026-06-15", every=1, paid=[
          {"on": "2026-07-12", "for": "2026-07-15", "amount": 1000}]),
          TODAY)["due"] == "2026-06-15")
check("a settled one-off has nothing outstanding at all",
      bills.due_info(bill(due="2026-07-01", every=0, paid=[
          {"on": "2026-07-01", "for": "2026-07-01", "amount": 1000}]),
          TODAY)["kind"] == "settled")
check("...and an unpaid one-off still is",
      bills.due_info(bill(due="2026-07-01", every=0), TODAY)["kind"]
      == "overdue")

# THE INVARIANT, over every state a bill can be in: the app must never point at
# an occurrence a payment has already been filed against.
bad = []
for anchor in ("2026-05-31", "2026-06-15", "2026-08-05", "2027-01-01"):
    for every in (0, 1, 2, 3, 6, 12):
        for n_paid in range(4):
            b = bill(due=anchor, every=every)
            occ = list(bills.occurrences(b, limit=8))[:n_paid]
            b["paid"] = [{"on": d, "for": d, "amount": 1000} for d in occ]
            got = bills.due_info(b, TODAY)
            if got["due"] is not None and got["due"] in occ:
                bad.append((anchor, every, n_paid, got["due"]))
check("the outstanding occurrence is never one already paid for", not bad,
      bad[:3])

check("every state names itself in words",
      all(bills.due_info(bill(due=d, method=m, lead=lead), TODAY)["state"]
          for d in ("2026-07-01", TODAY, "2026-08-06", "2026-08-09",
                    "2026-11-01")
          for m in bills.METHODS for lead in (0, 5)))
# Three different situations must give three different sentences. A test that
# only asks "is there a sentence" passes on code where every branch returns the
# same one, which is how a usbwriter bug shipped.
states = {bills.due_info(bill(due=d, method="mail", lead=5), TODAY)["state"]
          for d in ("2026-07-01", TODAY, "2026-08-09", "2026-11-01")}
check("...and four different situations give four different sentences",
      len(states) == 4, states)

check("what is due this month adds up the outstanding bills",
      bills.month_total([bill(due="2026-08-11", amount=8420),
                         bill(due="2026-08-20", amount=4165),
                         bill(due="2026-09-20", amount=99900)],
                        TODAY) == 12585,
      bills.month_total([bill(due="2026-08-11", amount=8420),
                         bill(due="2026-08-20", amount=4165),
                         bill(due="2026-09-20", amount=99900)], TODAY))
check("...including one that went unpaid in a previous month",
      bills.month_total([bill(due="2026-06-11", every=0, amount=8420)],
                        TODAY) == 8420)
check("...and a bill whose figure varies adds nothing rather than raising",
      bills.month_total([bill(due="2026-08-11", amount=None)], TODAY) == 0)


# -- 4. the store ------------------------------------------------------------
print("\n-- the store --")

put("bills.json", {"bills": [
    {"payee": "Good", "amount": 8420, "due": "2026-08-11"},
    {"payee": "", "amount": 100},                       # no payee: not a bill
    {"amount": 100},                                    # ditto
    "not even a record",
    {"payee": "Odd types", "amount": "84.20", "due": 7, "every": "x",
     "method": "carrier pigeon", "lead": -4, "address": 9, "paid": "nope"},
]})
got = bills.read_bills(os.path.join(CFG, "bills.json"))
check("a record with no payee is not a bill", len(got) == 2, got)
check("...and one bad record does not cost the good one",
      got[0]["payee"] == "Good" and got[0]["amount"] == 8420, got[0])
odd = got[1]
check("an amount stored as text is read as cents", odd["amount"] == 8420, odd)
check("an unreadable due date becomes today rather than dropping the bill",
      odd["due"] == bills.today_key(), odd["due"])
check("a repeat that is not a number falls back to monthly", odd["every"] == 1)
check("a method the app does not know falls back to post",
      odd["method"] == "mail")
check("a negative postal allowance is clamped, not honoured", odd["lead"] == 0)
check("a payment list of the wrong type costs only itself", odd["paid"] == [])
check("every bill has an id, and no two share one",
      all(b["id"] for b in got) and len({b["id"] for b in got}) == len(got))

put("bills.json", [{"payee": "Bare list", "amount": 500, "due": "2026-08-11"}])
check("a store that is a bare list is still read",
      [b["payee"] for b in bills.read_bills(os.path.join(CFG, "bills.json"))]
      == ["Bare list"])

put("bills.json", "{ truncated half way th")
check("an unparseable store reads as no bills, and does not raise",
      bills.read_bills(os.path.join(CFG, "bills.json")) == [])

put("bills.json", {"bills": [{"payee": "Kept", "amount": 500,
                              "due": "2026-08-11", "paid": [
                                  {"on": "2026-07-01", "amount": 500,
                                   "ref": "cheque 1042"}] * 40}]})
check("a long payment history is not truncated",
      len(bills.read_bills(os.path.join(CFG, "bills.json"))[0]["paid"]) == 40)

put("bills.json", {"bills": [{"payee": "Big", "amount": 500,
                              "due": "2026-08-11"}] * 260})
check("...nor is a long bill list",
      len(bills.read_bills(os.path.join(CFG, "bills.json"))) == 260)

# THE STORE THIS APP CANNOT RECOGNISE MUST SURVIVE BEING OPENED. Valid JSON in
# an unknown shape parses fine, the app opens empty, and the close-time flush
# then writes that emptiness over the user's only copy.
MARK = "PO Box 1188, Springfield"
put("bills.json", {"payments": [{"who": "City Light", "where": MARK}]})
w = bills.Bills()
w.destroy()
kept = [p for p in os.listdir(CFG) if p.startswith("bills.json.")]
found = False
for name in kept + ["bills.json"]:
    try:
        with open(os.path.join(CFG, name), encoding="utf-8") as fh:
            if MARK in fh.read():
                found = True
    except OSError:
        pass
check("a store in an unrecognised shape still exists after an open and close",
      found, sorted(os.listdir(CFG)))


# -- 5. round trip -----------------------------------------------------------
print("\n-- round trip --")

for name in os.listdir(CFG):
    os.remove(os.path.join(CFG, name))

w = bills.Bills()
check("the app opens with no store at all", w.bills == [])
w.bills.append(bills.normalise(
    {"payee": "City Light & Power", "account": "44-99", "amount": 8420,
     "due": "2026-08-11", "every": 1, "method": "mail", "lead": 5,
     "address": "PO Box 1188\nSpringfield IL 62705"}))
w.sel = w.bills[0]["id"]
w._save()
w.destroy()

w2 = bills.Bills()
check("a bill written by one run is there in the next", len(w2.bills) == 1,
      w2.bills)
back = w2.bills[0]
check("...with its amount, its date and its address intact",
      back["amount"] == 8420 and back["due"] == "2026-08-11"
      and "PO Box 1188" in back["address"], back)

# Recording a payment through the app's own path, not by editing the model.
before = bills.due_info(back)["due"]
back["paid"].append({"on": "2026-08-06", "for": before, "amount": 8420,
                     "method": "mail", "ref": "cheque 1042"})
w2._save()
w2.destroy()
w3 = bills.Bills()
check("a recorded payment moves the bill to its next date",
      bills.due_info(w3.bills[0])["due"] != before,
      (before, bills.due_info(w3.bills[0])["due"]))
check("...and the reference is kept with it",
      w3.bills[0]["paid"][0]["ref"] == "cheque 1042", w3.bills[0]["paid"])
w3.destroy()

# The app must not be the thing that destroys a store it CAN read.
raw_before = open(os.path.join(CFG, "bills.json"), encoding="utf-8").read()
w4 = bills.Bills()
w4.destroy()
raw_after = open(os.path.join(CFG, "bills.json"), encoding="utf-8").read()
check("opening and closing changes nothing on disk",
      json.loads(raw_before) == json.loads(raw_after))


# -- 6. the desktop tile -----------------------------------------------------
print("\n-- the desktop tile --")

import widgets                                                  # noqa: E402

check("the board knows about the tile",
      "bills" in widgets.TILE_ORDER
      and widgets.TILE_APP["bills"] == "bills"
      and "bills" in widgets.TILE_EMPTY, widgets.TILE_ORDER)

board = widgets.Widgets.__new__(widgets.Widgets)
got = board._read_bills()
check("the tile reads the same store the app wrote", got is not None, got)
if got:
    meta, rows, _cta = got
    check("...and heads it with what is owed this month",
          meta == bills.money(bills.month_total(bills.read_bills(
              os.path.join(CFG, "bills.json")))), meta)
    check("...and names the payee on a row", rows[0][1] == "City Light & Power",
          rows)

put("bills.json", {"bills": [
    {"payee": "Late", "amount": 100, "due": "2000-01-01", "every": 0},
    {"payee": "Later", "amount": 100, "due": "2999-01-01", "every": 0}]})
rows = board._read_bills()[1]
check("what needs paying comes first", rows[0][1] == "Late", rows)
check("...and is the row that is marked",
      isinstance(rows[0][2], tuple) and rows[0][2][0] == "alert", rows[0])
check("...while one that does not need paying is left quiet",
      not isinstance(rows[1][2], tuple), rows[1])

put("bills.json", "{ not json")
check("a damaged store leaves the tile with nothing to say, not a crash",
      board._read_bills() is None)
os.remove(os.path.join(CFG, "bills.json"))
check("...and so does no store at all", board._read_bills() is None)


# -- 7. dates in the reader's language ---------------------------------------
print("\n-- dates in the reader's language --")

# A DATE IS AN ORDER, NOT JUST A WORD. Japanese writes 8月11日 and Spanish
# writes "11 de agosto"; translating only the month name and pasting it into
# "%d %s" produced "11 8月", which is not a date in any language. nbi18n does
# the whole layout, but only for a string that is ENTIRELY a date, so this
# checks that fmt_date hands it one.
#
# In a SUBPROCESS: nbi18n reads NB_LANG once, at import.
import subprocess                                               # noqa: E402

PROBE = (
    "import bills, sys\n"
    "print(bills.fmt_date('2026-08-11'));"
    "print(bills.fmt_date('2026-07-06', year=True));"
    "print(bills.fmt_short('2026-08-11'))\n")
for lang, wants in (("ja", ("8月11日", "2026年7月6日", "8月11日")),
                    ("es", ("11 de agosto", "6 de julio de 2026", "11 ago")),
                    ("ru", ("11 август", "6 июль 2026", "11 авг")),
                    ("en", ("11 August", "6 July 2026", "11 Aug"))):
    env = dict(os.environ, NB_LANG=lang, PYTHONPATH=DE)
    out = subprocess.run([sys.executable, "-c", PROBE], env=env,
                         capture_output=True, text=True).stdout.split("\n")
    check("a date reads as a date in %s" % lang,
          [x.strip() for x in out[:3]] == list(wants), out[:3])


# -- done --------------------------------------------------------------------
shutil.rmtree(HOME, ignore_errors=True)
print()
if FAILED:
    print("bills selftest: %d FAILED" % len(FAILED))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("bills selftest: OK")
