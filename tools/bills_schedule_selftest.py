#!/usr/bin/env python3
"""When a bill is due, when it must be posted, and what this month costs.

    tools/guestrun.sh python3 tools/bills_schedule_selftest.py

`due_info`, `needs_paying`, `sort_key` and `month_total` are the whole domain
model of a bill tracker, and no suite had ever named `needs_paying`, `sort_key`
or `month_total`. They are also read by `widgets.py` for the desktop tile
through the same `read_bills()`/`due_info()` pair, on purpose, so that the tile
and the app can never disagree — which means a regression here is wrong in two
places at once and this file guards both.

THIS SUITE PINS BEHAVIOUR THAT IS ALREADY CORRECT. Measured across ten bill
shapes and thirteen month-boundary cases, nothing was wrong. That is worth
saying plainly, because a suite arriving with no defect attached is usually a
suite too weak to find one — the red proofs at the bottom are the evidence that
this one is not.

THE TWO INVARIANTS. Neither is expressible as a single expected value, which is
why they had gone unchecked:

  1. URGENCY NEVER GOES BACKWARDS. Walk a bill day by day toward its due date
     and the state may only become more urgent: later -> soon -> post -> today
     -> overdue. A tracker that told somebody "Post now" on Tuesday and "Due in
     5 days" on Wednesday would be worse than one that said nothing.
  2. ONCE IT NEEDS PAYING, IT KEEPS NEEDING PAYING until a payment is filed.
     `needs_paying` drives the sidebar count, the desktop tile and the sort
     order, so a flip back to False makes a bill vanish from all three.

A POSTED BILL IS NOT DUE WHEN IT IS DUE. The state a person needs is the
nearest DEADLINE, and for a cheque that is the post date, not the due date: a
bill due in three days with five days to allow in the post is not "due in 3
days", it is already too late to post. That is why "post" ranks above "soon".

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. "Post now" stops firing on the day itself
     (`elif post_days is not None and post_days <= 0:` -> `... < 0:`)
       FAIL on 2026-09-10 a posted cheque reads 'Post now'
            <- Post in 0 days
     Caught by the literal day-by-day states, NOT by the monotonicity check:
     "Post in 0 days" is still `post`, so urgency never moved backwards. An
     invariant and the actual words both have to be pinned.
  2. the month bound excludes its own last day (`if o is None or o > end:`
     -> `if o is None or o >= end:`)                          3 FAILED
       FAIL due on the LAST day of the month
            <- got 0, expected 5000 (today=2026-08-10)
       FAIL leap-year 29 Feb is inside February
       FAIL non-leap 28 Feb is the last day
  3. "post" is dropped from the acting set
     (`ACTIVE_KINDS = ("overdue", "today", "post")` -> `("overdue", "today")`)
       FAIL ...and needs_paying is True  <- False for state 'Post in 2 days'
       FAIL ...and needs_paying is True  <- False for state 'Post now'

     THIS PROOF FOUND A HOLE IN THIS FILE. On its first run it left all 48
     checks GREEN. Dropping "post" makes `needs_paying` go False EARLIER, which
     is neither a backward step in urgency nor a True->False flip — so both
     invariants passed while a cheque that had to go in today's post had
     silently vanished from the sidebar count, the desktop tile and the top of
     the sort order. The explicit state/needs_paying table exists because of
     this proof, not before it. **An invariant about how a value CHANGES cannot
     see a change in which values it takes.**
  4b. each occurrence is a month past the PREVIOUS one, not the anchor
     (`add_months(anchor, n * every)` -> carried forward from the last result)
                                                                4 FAILED
       FAIL the 31st, monthly runs 2026-01-31 2026-02-28 2026-03-31 2026-04-30
            <- ['2026-01-31', '2026-02-28', '2026-03-28', '2026-04-28']
       FAIL ...and it returns to the 31st after a short month   <- [28]
       (and the same pair for the 30th)

     THIS PROOF STRENGTHENED THIS FILE. The "returns to the anchor day" check
     first read `max(seq)`, which the ANCHOR ITSELF satisfies — so it stayed
     green while every occurrence after February was pinned to the 28th for
     good. It measures `seq[1:]` now. **A claim that a value COMES BACK cannot
     be tested on a series that starts at it.**
  4c. the day of the month is not clamped into its month
     (`min(d, _month_len(y2, m2))` -> `d`)                       8 FAILED
       FAIL ...and every occurrence is a real date
            <- ['2026-01-31', '2026-02-31', '2026-03-31', ...]
  4. a repeating bill only ever yields its anchor
     (`for n in range(limit):` in `occurrences` -> `for n in range(1)`)
       FAIL a bill paid every month since 2019 has advanced to the current one
            <- (None, 91)
       FAIL ...and reads as due, not overdue and not paid
            <- ('settled', 'Paid')

     Note the SHAPE of that failure and why the aged cases are worth their
     lines: a bill with 91 payments filed and one still owing reads as **Paid**.
     Running out of occurrences does not look like an error — it looks like
     good news, and it is the one wrong answer a bill tracker must never give.

BILLS OLDER THAN THE APP are ordinary: imported, or one somebody stopped paying.
The outstanding occurrence is the EARLIEST with no payment against it, so an old
unpaid bill must read Overdue and count ONCE toward this month, not once per
missed month.
"""
import os
import sys
import shutil

H = "/tmp/nbhome-billssched-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("BILLS_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import bills                                                  # noqa: E402

R = []

# More urgent = higher. The order the app itself commits to.
RANK = {"settled": 0, "later": 1, "soon": 2, "post": 3, "today": 4,
        "overdue": 5}


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def B(tag, amount, due, every=1, paid=None, method="phone", lead=0):
    return dict(id=tag, payee=tag, account="", amount=amount, due=due,
                every=every, method=method, address="A", phone="", note="",
                lead=lead, paid=paid or [])


# --------------------------------------------- 1. urgency is monotone in time
SHAPES = (("mail", 5, 1), ("mail", 7, 1), ("mail", 1, 1), ("mail", 0, 1),
          ("phone", 0, 1), ("person", 0, 1), ("auto", 0, 1),
          ("mail", 5, 0), ("mail", 10, 1), ("mail", 30, 1))

for method, lead, every in SHAPES:
    tag = "%s lead=%d every=%d" % (method, lead, every)
    b = B("x", 5000, "2026-09-15", every=every, method=method, lead=lead)
    rows = []
    for d in range(20, -3, -1):
        today = bills.add_days("2026-09-15", -d)
        info = bills.due_info(b, today=today)
        rows.append((today, info["kind"], info["state"],
                     bills.needs_paying(info)))

    back = None
    flip = None
    for (t1, k1, s1, n1), (t2, k2, s2, n2) in zip(rows, rows[1:]):
        if back is None and RANK[k2] < RANK[k1]:
            back = "%s(%s) -> %s(%s) at %s" % (k1, s1, k2, s2, t2)
        if flip is None and n1 and not n2:
            flip = "True -> False at %s (%s -> %s)" % (t2, s1, s2)
    check("%s: urgency never goes backwards" % tag, back is None, back or "")
    check("%s: once it needs paying it keeps needing paying" % tag,
          flip is None, flip or "")

# The flagship shape, asserted line by line: a monthly cheque with five days to
# allow in the post. These exact words are what the sidebar and the tile show.
b = B("x", 5000, "2026-09-15", every=1, method="mail", lead=5)


def state_on(day):
    return bills.due_info(b, today=day)["state"]


for day, want in (("2026-09-06", "Due 15 Sep"), ("2026-09-07", "Post in 3 days"),
                  ("2026-09-08", "Post in 2 days"),
                  ("2026-09-09", "Post tomorrow"), ("2026-09-10", "Post now"),
                  ("2026-09-14", "Post now"), ("2026-09-15", "Due today"),
                  ("2026-09-16", "Overdue")):
    check("on %s a posted cheque reads %r" % (day, want),
          state_on(day) == want, state_on(day))

# ...and the post-by date itself is the due date less the lead, not anything
# derived from when it is looked at.
info = bills.due_info(b, today="2026-09-01")
check("post_by is the due date less the lead days",
      info["post_by"] == "2026-09-10", info["post_by"])
check("...and it does not move as the days pass",
      bills.due_info(b, today="2026-09-14")["post_by"] == "2026-09-10",
      bills.due_info(b, today="2026-09-14")["post_by"])

# A bill posted with NO lead has no post-by date at all.
nolead = B("y", 5000, "2026-09-15", method="mail", lead=0)
check("a posted bill with no lead days has no post-by date",
      bills.due_info(nolead, today="2026-09-01")["post_by"] is None)

# WHICH STATES ACTUALLY COUNT AS NEEDING ACTION. The two invariants above cannot
# see this: dropping "post" from ACTIVE_KINDS makes needs_paying go False
# EARLIER, which is neither a backward step in urgency nor a True->False flip,
# so both stayed green while a cheque that had to go in today's post vanished
# from the sidebar count, the desktop tile and the top of the sort order.
# Measured — that mutation left this file at 48 checks, 0 failed.
for day, kind, want in (("2026-09-06", "later", False),
                        ("2026-09-08", "post", True),
                        ("2026-09-10", "post", True),
                        ("2026-09-15", "today", True),
                        ("2026-09-16", "overdue", True)):
    info = bills.due_info(b, today=day)
    check("on %s the state is %r" % (day, kind), info["kind"] == kind,
          info["kind"])
    check("...and needs_paying is %s" % want,
          bills.needs_paying(info) is want,
          "%s for state %r" % (bills.needs_paying(info), info["state"]))

# "soon" is the one non-acting state a bill passes through on a NON-posted
# bill, and it must not count either.
soon = B("z", 5000, "2026-09-15", every=1, method="phone", lead=0)
si = bills.due_info(soon, today="2026-09-12")
check("a phone bill three days out is 'soon'", si["kind"] == "soon", si["kind"])
check("...and 'soon' does not count as needing paying",
      bills.needs_paying(si) is False, si["state"])
settled = bills.due_info(
    B("s", 100, "2026-08-01", every=0,
      paid=[{"on": "2026-08-01", "for": "2026-08-01", "amount": 100,
             "method": "phone", "ref": ""}]), today="2026-08-10")
check("a settled bill does not count as needing paying",
      bills.needs_paying(settled) is False, settled["state"])

# ------------------------------------------------------ 2. what this month costs
MT = (
    ("2026-08-10", [B("a", 5000, "2026-08-15", every=0)], 5000,
     "one-off due later this month"),
    ("2026-08-10", [B("a", 5000, "2026-08-31", every=0)], 5000,
     "due on the LAST day of the month"),
    ("2026-08-10", [B("a", 5000, "2026-09-01", every=0)], 0,
     "due on the 1st of NEXT month"),
    ("2026-08-10", [B("a", 5000, "2026-07-20", every=0)], 5000,
     "OVERDUE from last month still counts"),
    ("2026-08-10", [B("a", None, "2026-08-15", every=0)], 0,
     "an amount that varies contributes nothing"),
    ("2026-08-10", [B("a", 5000, "2026-08-15", every=0,
                      paid=[{"on": "2026-08-01", "for": "2026-08-15",
                             "amount": 5000, "method": "phone", "ref": ""}])],
     0, "already paid contributes nothing"),
    ("2026-08-10", [B("a", 5000, "2026-08-15", every=0),
                    B("b", 2500, "2026-08-20", every=0),
                    B("c", None, "2026-08-25", every=0),
                    B("d", 9900, "2026-09-05", every=0)], 7500,
     "a mixed book adds only what is inside the month"),
    ("2028-02-10", [B("a", 5000, "2028-02-29", every=0)], 5000,
     "leap-year 29 Feb is inside February"),
    ("2027-02-10", [B("a", 5000, "2027-02-28", every=0)], 5000,
     "non-leap 28 Feb is the last day"),
    ("2026-12-20", [B("a", 5000, "2026-12-31", every=0)], 5000,
     "31 Dec counts in December"),
    ("2026-12-20", [B("a", 5000, "2027-01-01", every=0)], 0, "1 Jan does not"),
    ("2026-08-10", [B("a", 5000, "2026-08-15", every=1)], 5000,
     "a monthly bill counts once, not once per future occurrence"),
    ("2026-08-01", [B("a", 5000, "2026-08-01", every=1)], 5000,
     "due TODAY counts"),
)
for today, rows, want, why in MT:
    got = bills.month_total(rows, today=today)
    check(why, got == want, "got %r, expected %r (today=%s)"
          % (got, want, today))

# ------------------------------------------------------------ 3. the sort order
# Outstanding first, soonest first, settled last; ties break on the payee so a
# list of bills all due on the 1st holds still between refreshes.
book = [B("zebra", 100, "2026-08-20", every=0),
        B("apple", 100, "2026-08-20", every=0),
        B("mango", 100, "2026-08-05", every=0),
        B("settled", 100, "2026-08-01", every=0,
          paid=[{"on": "2026-08-01", "for": "2026-08-01", "amount": 100,
                 "method": "phone", "ref": ""}])]
order = [b["payee"] for b in sorted(
    book, key=lambda x: bills.sort_key(x, bills.due_info(x, "2026-08-10")))]
check("the soonest outstanding bill sorts first", order[0] == "mango", order)
check("a settled bill sorts last", order[-1] == "settled", order)
check("bills due the same day break the tie on the payee",
      order.index("apple") < order.index("zebra"), order)
check("...and the order is stable across repeated sorts",
      order == [b["payee"] for b in sorted(
          book, key=lambda x: bills.sort_key(
              x, bills.due_info(x, "2026-08-10")))], order)

# ------------------------------------------------- bills older than the app
# A bill anchored years back is ordinary: imported, or one somebody stopped
# paying. The outstanding occurrence is the EARLIEST one no payment is filed
# against, so an old unpaid bill must read Overdue — never "Paid", which is what
# a loader that ran out of occurrences, or a `paid_for` that matched too
# eagerly, would show. Silent, and exactly backwards.
old = B("old", 5000, "2019-01-15", every=1)
info = bills.due_info(old, today="2026-08-08")
check("a monthly bill unpaid since 2019 is still Overdue",
      info["kind"] == "overdue", (info["kind"], info["state"]))
check("...and its outstanding occurrence is the ANCHOR, not something later",
      info["due"] == "2019-01-15", info["due"])
check("...and it counts once toward this month, not once per missed month",
      bills.month_total([old], today="2026-08-08") == 5000,
      bills.month_total([old], today="2026-08-08"))

once = B("once", 5000, "2019-03-01", every=0)
check("a one-off from 2019 is Overdue, not settled",
      bills.due_info(once, today="2026-08-08")["kind"] == "overdue",
      bills.due_info(once, today="2026-08-08")["kind"])

# ...and one paid every month since 2019 must have walked all the way forward.
paid = []
y, m = 2019, 1
while (y, m) < (2026, 8):
    day = "%04d-%02d-15" % (y, m)
    paid.append({"on": day, "for": day, "amount": 5000, "method": "phone",
                 "ref": ""})
    m += 1
    if m > 12:
        m, y = 1, y + 1
kept = B("kept", 5000, "2019-01-15", every=1, paid=paid)
info = bills.due_info(kept, today="2026-08-08")
check("a bill paid every month since 2019 has advanced to the current one",
      info["due"] == "2026-08-15", (info["due"], len(paid)))
check("...and reads as due, not overdue and not paid", info["kind"] == "soon",
      (info["kind"], info["state"]))

# A one-off with its single payment filed has nowhere left to go.
settled = B("settled", 5000, "2019-03-01", every=0,
            paid=[{"on": "2019-03-01", "for": "2019-03-01", "amount": 5000,
                   "method": "phone", "ref": ""}])
check("a settled one-off from 2019 reads as Paid",
      bills.due_info(settled, today="2026-08-08")["kind"] == "settled",
      bills.due_info(settled, today="2026-08-08")["kind"])

# ------------------------------------- a bill due on the 31st does not DRIFT
# `add_months`' own docstring states the rule: "the day of the month is a rule,
# not an offset, so the clamp is applied to the ANCHOR every time rather than
# carried forward", and names both wrong answers — adding 30 days walks a
# monthly bill backwards through the year, and adding one month to the PREVIOUS
# RESULT pins a 31st bill to the 28th for good after one February.
#
# Both are silent: every date produced is a real date, in roughly the right
# week, and nothing looks broken until somebody notices their rent is due on
# the 28th forever.


def occ(due, every, n=14):
    b = B("x", 100, due, every=every)
    out = []
    for i, day in enumerate(bills.occurrences(b)):
        out.append(day)
        if i >= n - 1:
            break
    return out


for due, every, want_head, why in (
        ("2026-01-31", 1,
         ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"],
         "the 31st, monthly"),
        ("2026-01-30", 1,
         ["2026-01-30", "2026-02-28", "2026-03-30", "2026-04-30"],
         "the 30th, monthly"),
        ("2026-03-31", 2,
         ["2026-03-31", "2026-05-31", "2026-07-31", "2026-09-30"],
         "the 31st, every two months"),
        ("2028-02-29", 12,
         ["2028-02-29", "2029-02-28", "2030-02-28", "2031-02-28"],
         "29 Feb, yearly, from a leap year")):
    seq = occ(due, every)
    check("%s runs %s" % (why, " ".join(want_head)),
          seq[:len(want_head)] == want_head, seq[:len(want_head)])
    check("...and every occurrence is a real date",
          all(bills._parts(d) is not None for d in seq), seq)
    # The anchor day RETURNS: a short month must not capture the bill forever.
    # Measured over seq[1:], EXCLUDING the anchor — `max(seq)` is satisfied by
    # the anchor itself, so it stayed green under the drift proof while every
    # occurrence after February was pinned to the 28th. The claim is that the
    # day comes BACK, and only the tail can say that.
    anchor_day = int(due[8:])
    _ord = {1: "st", 2: "nd", 3: "rd"}.get(
        anchor_day if anchor_day < 20 else anchor_day % 10, "th")
    check("...and it returns to the %d%s after a short month"
          % (anchor_day, _ord),
          max(int(d[8:]) for d in seq[1:]) == anchor_day,
          sorted(set(int(d[8:]) for d in seq[1:])))
    check("...and never runs past the anchor day",
          all(int(d[8:]) <= anchor_day for d in seq),
          [d for d in seq if int(d[8:]) > anchor_day])

# Occurrences are strictly increasing — a repeat that ever goes backwards would
# make `due_info` pick an occurrence it had already passed.
seq = occ("2026-01-31", 1, n=26)
check("occurrences only ever move forward", seq == sorted(seq),
      [ (a, b) for a, b in zip(seq, seq[1:]) if b <= a ][:2])

# ------------------------------------------------- how money is written down
# `money()` and `parse_money()` were named by no suite. A mutation sweep over
# the domain logic found `sign = "-" if n < 0 else ""` SURVIVING: flip it to
# `<=` and every zero renders as MINUS zero. A $0.00 bill is reachable (a
# correction, a waived charge), and "-$0.00" in a bill tracker is the kind of
# wrong that makes somebody distrust every other figure on the screen.
#
# `money()`'s own docstring says it must match `accounting._money` because "the
# two apps are read on the same desk and must not disagree about what money
# looks like" — so these are the shared shape, not a private detail.
for cents, want in ((0, "$0.00"), (1, "$0.01"), (-1, "\u2212$0.01"),
                    (100, "$1.00"), (-100, "\u2212$1.00"),
                    (8420, "$84.20"), (-8420, "\u2212$84.20"),
                    (123456789, "$1,234,567.89"),
                    (-123456789, "\u2212$1,234,567.89")):
    check("money(%d) is %r" % (cents, want), bills.money(cents) == want,
          bills.money(cents))
check("ZERO carries no sign at all", bills.money(0) == "$0.00",
      bills.money(0))
check("...and neither does a value that is not a number",
      bills.money(None) == "$0.00" and bills.money("x") == "$0.00",
      (bills.money(None), bills.money("x")))
check("the thousands separator appears above 999.99",
      "," in bills.money(100000), bills.money(100000))

# Typed money becomes whole CENTS, rounded half-up at the third decimal — the
# boundary a sweep also found unguarded.
for text, want in (("8.40", 840), ("8.404", 840), ("8.405", 841),
                   ("8.406", 841), ("0.004", 0), ("0.005", 1),
                   ("12.345", 1235), ("1.999", 200), ("84.20", 8420),
                   ("$84.20", 8420)):
    check("parse_money(%r) is %r" % (text, want),
          bills.parse_money(text) == want, bills.parse_money(text))
for text in ("", "   ", "room 12", "12-34", "abc"):
    check("parse_money(%r) refuses" % text, bills.parse_money(text) is None,
          bills.parse_money(text))

# A figure written down and read back must be the same figure.
for cents in (0, 1, 999, 8420, 100000, 123456789):
    back = bills.parse_money(bills.money(cents).replace("$", ""))
    check("money(%d) reads back as itself" % cents, back == cents,
          (bills.money(cents), back))

# ------------------------------------------------- the day arithmetic itself
# `add_days` is what turns a lead into a POST-BY date (`add_days(due, -lead)`),
# so it computes the one figure this app exists to produce. It was pinned only
# at the two points the post-by cases happen to touch.
#
# A mutation sweep found the civil-from-days conversion inside
# `_day_from_ordinal` SURVIVING — and, worse, I dismissed those survivors as
# "equivalent mutants" by reading them. Re-checked by running the mutated module
# against a battery of real calls: 7 of the 16 I had waved away CHANGE
# BEHAVIOUR, `add_days` among them, wrong by a day across month and year edges.
# **"Equivalent mutant" is a claim, and it needs measuring like any other.**
for day, n, want in (("2026-01-31", 1, "2026-02-01"),
                     ("2026-01-31", -1, "2026-01-30"),
                     ("2026-12-31", 1, "2027-01-01"),
                     ("2027-01-01", -1, "2026-12-31"),
                     ("2028-02-28", 1, "2028-02-29"),
                     ("2028-02-29", 1, "2028-03-01"),
                     ("2100-02-28", 1, "2100-03-01"),
                     ("2000-02-28", 1, "2000-02-29"),
                     ("2026-06-15", 400, "2027-07-20"),
                     ("2026-06-15", -400, "2025-05-11"),
                     ("2026-03-01", -1, "2026-02-28")):
    check("add_days(%s, %+d) is %s" % (day, n, want),
          bills.add_days(day, n) == want, bills.add_days(day, n))

# A day out and back is the same day — true across every edge above, and the
# cheapest way to catch a conversion that is asymmetric.
for day in ("2026-01-31", "2026-12-31", "2028-02-29", "2100-03-01",
            "2000-02-29", "2026-06-15"):
    for n in (1, -1, 31, -31, 365, -365):
        there = bills.add_days(day, n)
        back = bills.add_days(there, -n) if there else None
        check("%s %+d and back is itself" % (day, n), back == day,
              (there, back))

# The written forms, which the same guards feed.
for day, long_, withyear, short in (
        ("2026-01-31", "31 January", "31 January 2026", "31 Jan"),
        ("2026-12-31", "31 December", "31 December 2026", "31 Dec"),
        ("2028-02-29", "29 February", "29 February 2028", "29 Feb")):
    check("fmt_date(%s) is %r" % (day, long_),
          bills.fmt_date(day) == long_, bills.fmt_date(day))
    check("...with a year, %r" % withyear,
          bills.fmt_date(day, year=True) == withyear,
          bills.fmt_date(day, year=True))
    check("...and short, %r" % short, bills.fmt_short(day) == short,
          bills.fmt_short(day))

# An unreadable date must not become a plausible one.
for junk in ("", "next tuesday", "2026-13-01", "2026-00-10", None, 7):
    check("add_days(%r) refuses rather than inventing" % (junk,),
          bills.add_days(junk, 1) is None, bills.add_days(junk, 1))

# WHERE THE GUARD ACTUALLY IS, since this suite first asserted it in the wrong
# place. `add_days` goes through `_ordinal`, which NORMALISES an out-of-range
# day — add_days("2026-02-30", 1) is "2026-03-03", not None. That is not a
# defect and not reachable: the boundary guard is `_parts`, which REJECTS such a
# date, and every caller crosses it first. Asserting the leniency where it lives
# and the rejection where it lives says what is actually true.
for impossible in ("2026-02-30", "2026-02-31", "2026-04-31", "2026-01-32"):
    check("_parts(%r) rejects an impossible day" % impossible,
          bills._parts(impossible) is None, bills._parts(impossible))
    check("...and it is never written out as a date",
          bills.fmt_date(impossible) == "" and bills.fmt_short(impossible) == "",
          (bills.fmt_date(impossible), bills.fmt_short(impossible)))

# ...so a stored bill carrying one gets today, as `normalise` documents, rather
# than a due date that does not exist.
_imp = dict(id="x", payee="P", account="", amount=5000, due="2026-02-30",
            every=1, method="phone", address="", phone="", note="", lead=0,
            paid=[])
_n = bills.normalise(_imp)
check("a bill stored with an impossible due date is KEPT",
      _n is not None and _n["payee"] == "P", _n)
check("...with its due date replaced by today, not by 30 February",
      _n["due"] == bills.today_key(), _n["due"])

# ------------------------------------------------------- the year, or not
# `fmt_due` carries a date's year ONLY when that year is not the current one:
# "on a bill due in three weeks the year is noise on every line it appears on;
# on one due in fourteen months it is the whole point." One function so the
# header, the action band and the payment sheet cannot disagree.
#
# It was named by no suite, which is why TWO sweep survivors lived in it — and
# why my own equivalence battery reported one of them "equivalent": the battery
# never called `fmt_due`. **A battery only certifies what it exercises**, so a
# survivor inside an unexercised function is not evidence of equivalence.
_this = int(bills.today_key()[:4])
for day, want_year in (("%d-08-15" % _this, False),
                       ("%d-01-01" % _this, False),
                       ("%d-12-31" % _this, False),
                       ("%d-01-01" % (_this + 1), True),
                       ("%d-12-31" % (_this - 1), True),
                       ("%d-06-15" % (_this + 3), True)):
    got = bills.fmt_due(day)
    has_year = str(_parts_year := day[:4]) in got
    check("fmt_due(%s) %s its year" % (day, "carries" if want_year else "omits"),
          has_year is want_year, got)
    check("...and still names the day and month",
          got.split()[0] == str(int(day[8:])) and len(got.split()) >= 2, got)

check("a date in this year is the bare day and month",
      bills.fmt_due("%d-08-15" % _this) == bills.fmt_date("%d-08-15" % _this),
      bills.fmt_due("%d-08-15" % _this))
check("...and one in another year is the long form",
      bills.fmt_due("%d-08-15" % (_this + 1))
      == bills.fmt_date("%d-08-15" % (_this + 1), year=True),
      bills.fmt_due("%d-08-15" % (_this + 1)))

for junk in ("", "2026-02-30", "next tuesday", None, 7):
    check("fmt_due(%r) is empty rather than invented" % (junk,),
          bills.fmt_due(junk) == "", bills.fmt_due(junk))

# Transient status callbacks are owned and cannot outlive the Bills window.
class _Status:
    def __init__(self): self.text = ""
    def set_text(self, text): self.text = text


_app = bills.Bills.__new__(bills.Bills)
_app.status = _Status()
_app._flash_id = 0
_app._flash_timer = 0
_app._closed = False
_refreshes = []
_app._refresh_status = lambda: _refreshes.append(True)
_timers, _removed = [], []
_real_timeout = bills.GLib.timeout_add_seconds
_real_remove = bills.GLib.source_remove
bills.GLib.timeout_add_seconds = lambda _delay, callback: (_timers.append(callback) or len(_timers))
bills.GLib.source_remove = lambda source_id: _removed.append(source_id) or True
try:
    _app._flash("Paid")
    _app._flash("Undone")
    check("a newer bill status replaces the prior restore timer",
          _removed == [1] and _app._flash_timer == 2,
          (_removed, _app._flash_timer))
    _app._on_destroy()
    check("closing Bills cancels its live status timer",
          _removed == [1, 2] and _app._flash_timer == 0 and _app._closed,
          (_removed, _app._flash_timer))
    _timers[-1]()
    check("a dispatched status callback after close is inert",
          not _refreshes, _refreshes)
finally:
    bills.GLib.timeout_add_seconds = _real_timeout
    bills.GLib.source_remove = _real_remove

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
