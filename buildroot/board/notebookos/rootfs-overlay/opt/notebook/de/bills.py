#!/usr/bin/env python3
"""
Bill Tracker — what is owed, when it is due, and how each one gets paid.

Two halves, and the second is the reason the app exists. Every other money app
on this machine records what HAPPENED (accounting.py keeps the cash book). This
one carries what has to happen next, and the instructions for doing it: the
remittance address to post a cheque to, the number to ring, the account number
to quote, and the reference that comes back afterwards.

There is no network on this machine, so a bill is paid by post, over the phone,
across a counter, or by a standing instruction the bank already holds. Those
four are the whole of `METHODS`, and the two the app is built around are the
first two:

  * BY POST a cheque has to leave before the due date, not on it. Each posted
    bill carries a `lead` (days to allow in the post) and the screen states the
    POST BY date as its own deadline — `due` minus `lead`. Missing that date is
    the failure this app is meant to prevent, and it is invisible on a wall
    calendar, which can only show the due date.
  * BY PHONE the number and the account number have to be to hand before
    dialling, and the confirmation number given at the end is the only proof
    the payment was made. Both are fields, not notes.

A bill repeats: `due` is the ANCHOR date and `every` the months between
occurrences (0 = a one-off). What is outstanding is the earliest occurrence no
payment is filed against, so recording a payment moves the bill on by itself
and nothing has to be edited each month. Occurrence dates are computed by
calendar arithmetic with the day CLAMPED into the month (a bill due on the 31st
falls on the 28th in February), never by adding 30 days.

Data lives in $NB_HOME/.config/notebook/bills.json:

    {"bills": [{"id": .., "payee": .., "account": .., "amount": 8420,
                "due": "2026-08-15", "every": 1, "method": "mail",
                "address": .., "phone": .., "note": .., "lead": 5,
                "paid": [{"on": "2026-07-12", "for": "2026-07-15",
                          "amount": 8420, "method": "mail",
                          "ref": "cheque 1042"}]}]}

Amounts are whole CENTS, never floats: a bill is money, and 0.1 + 0.2 is not
0.3. `amount: null` is a bill whose figure changes every time (a phone bill),
which the screen says rather than showing a wrong number.

There is one store, autosaved — no file management, so the File menu carries no
Save (see docs/MENU-CONVENTIONS.md rule 2). widgets.py reads the same store
through read_bills()/due_info() below, so the desktop tile and the app can
never disagree about what is due.
"""
import os
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, GLib, Pango  # noqa: E402

import nbapp  # noqa: E402
import nbicons  # noqa: E402
import nbprint  # noqa: E402
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STORE = os.path.join(CFG_DIR, "bills.json")
PDF_NAME = "Bills.pdf"
DOCS_DIR = os.path.join(HOME, "Documents")

SIDEBAR_W = 252
# The reading column the detail pane is held to. A bill is a short record; run
# across the full width of a 1920 panel its four facts sit a foot apart and
# stop reading as one thing.
COLUMN_W = 820
# The remittance address is set as an address, in a block the width of a real
# envelope panel, with the facts beside it rather than under it.
ADDRESS_W = 330

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
# The three-letter forms, for the places a full month name does not fit (the
# sidebar row, the desktop tile). Every one of these is already a catalog key.
MON_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
            "Oct", "Nov", "Dec")

# How a bill gets paid. The id is what the store holds and never changes; the
# label is what the screen says. Post and phone are first because they are what
# this machine's owner actually does — the other two exist so that a bill which
# is NOT paid that way can still be tracked here instead of being kept on a
# separate piece of paper.
METHODS = ("mail", "phone", "person", "auto")
METHOD_LABEL = {"mail": "By post", "phone": "By phone",
                "person": "In person", "auto": "Automatic"}
# The heading over the instructions block, per method.
METHOD_HEAD = {"mail": "HOW TO PAY BY POST", "phone": "HOW TO PAY BY PHONE",
               "person": "HOW TO PAY IN PERSON", "auto": "HOW IT IS PAID"}
# What the reference recorded against a payment IS, per method. A cheque number
# and a confirmation number are different things and a field called "Reference"
# asks for neither of them.
REF_LABEL = {"mail": "Cheque number", "phone": "Confirmation number",
             "person": "Receipt number", "auto": "Reference"}

# How often a bill comes round, in months. 0 is the one-off: a bill that is
# paid once and then done, which is a different thing from one that repeats.
REPEATS = (1, 2, 3, 6, 12, 0)
REPEAT_LABEL = {1: "Every month", 2: "Every 2 months", 3: "Every 3 months",
                6: "Every 6 months", 12: "Every year", 0: "Once"}

# Store limits. These bound what a DAMAGED file can inflate into; they are
# deliberately not caps on how much a person may keep, and nothing here ever
# truncates a list of records (see nbapp / the record-loss selftest: a loader
# that ended `return out[:200]` lost a student sixty assignments to an open and
# a close).
MAX_TEXT = 400
MAX_LEAD = 60
MAX_CENTS = 10 ** 11          # a hundred million in currency units

# A walk over occurrences has to stop even when the store says `every: 1` and
# an anchor from the year 1200. Two thousand steps is a hundred and sixty years
# of monthly bills; past that the anchor is not a date anybody typed.
_MAX_STEPS = 2000

# How near the due date a bill starts being called out on the sidebar and the
# desktop tile.
SOON_DAYS = 7


# ---------------------------------------------------------------- dates
# Plain civil-date arithmetic throughout. NEVER time.strptime and never
# `import calendar`: the Calendar app's calendar.py shadows the stdlib module
# on PYTHONPATH, so a call into it crashes whichever app made it. And stepping
# a date by 86400 seconds slips an hour across a daylight-saving change, which
# is exactly the kind of silent one-day error that makes a bill late.

_ordinal = nbapp.day_ordinal
# The same thing under a name a caller outside this file can use: days since
# 1970-01-01 for a "YYYY-MM-DD" key, or None if it is not one. Subtracting two
# of these is the only correct way to ask how many days apart two dates are.
day_of = nbapp.day_ordinal


def today_key(when=None):
    t = time.localtime(when) if when is not None else time.localtime()
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def _parts(day):
    """(y, m, d) for a "YYYY-MM-DD" key, or None when it is not one.

    Validated through day_ordinal, so "2026-02-31" is rejected here rather than
    quietly becoming a date in March further down."""
    try:
        y, m, d = (int(p) for p in str(day).split("-"))
    except (TypeError, ValueError):
        return None
    if _ordinal("%04d-%02d-%02d" % (y, m, d)) is None:
        return None
    if not 1 <= d <= _month_len(y, m):
        return None
    return y, m, d


def _month_len(year, month):
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def add_months(day, n):
    """`day` moved on by n whole months, with the day of the month CLAMPED.

    A bill due on the 31st is due on the 28th of February and on the 31st again
    in March — the day of the month is a rule, not an offset, so the clamp is
    applied to the ANCHOR every time rather than carried forward. Adding 30 days
    instead walks a monthly bill backwards through the year and lands it on a
    different date every month, and adding one month at a time from the previous
    result pins a 31st bill to the 28th for good after one February."""
    p = _parts(day)
    if p is None:
        return None
    y, m, d = p
    total = (y * 12 + (m - 1)) + int(n)
    y2, m2 = total // 12, total % 12 + 1
    if not 1 <= y2 <= 9999:
        return None
    return "%04d-%02d-%02d" % (y2, m2, min(d, _month_len(y2, m2)))


def add_days(day, n):
    o = _ordinal(day)
    return None if o is None else _day_from_ordinal(o + int(n))


def _day_from_ordinal(o):
    """The "YYYY-MM-DD" key for a day number — the inverse of day_ordinal."""
    z = int(o) + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return "%04d-%02d-%02d" % (y + (m <= 2), m, d)


# A DATE IS BUILT IN ENGLISH AND THEN TRANSLATED WHOLE. Not month-by-month:
# nbi18n recognises a string that is ENTIRELY a date and lays it out the way
# the language writes one, which is a different ORDER, not a different word.
# Translating only the month name and pasting it into "%d %s" gave Japanese
# "11 8月" where a date is written "8月11日", Spanish "11 Agosto" where it is
# "11 de agosto", and Korean "11 8월" for "8월 11일". Handing over the finished
# English string lets that machinery do its job. (Composing the whole SENTENCE
# in English and translating that would be better still -- nbi18n can match a
# formatted string back against its catalog pattern -- but it needs five
# non-space literal characters to do it safely, and "Due %s" has three.)

def fmt_date(day, year=False):
    """"15 August", or "15 August 2026" with `year`, in the reader's language.
    Empty for a date this app cannot parse."""
    p = _parts(day)
    if p is None:
        return ""
    y, m, d = p
    out = "%d %s" % (d, MONTHS[m - 1])
    return _t("%s %d" % (out, y) if year else out)


def fmt_short(day):
    """"15 Aug" — for a column too narrow for a month name."""
    p = _parts(day)
    if p is None:
        return ""
    return _t("%d %s" % (p[2], MON_ABBR[p[1] - 1]))


def fmt_due(day):
    """A due date, carrying its year only when that year is not this one.

    On a bill due in three weeks the year is noise on every line it appears
    on; on one due in fourteen months it is the whole point. One function, so
    the header, the action band and the payment sheet cannot end up disagreeing
    about which dates are worth a year."""
    p, this = _parts(day), _parts(today_key())
    if p is None:
        return ""
    return fmt_date(day, year=(this is None or p[0] != this[0]))


# ---------------------------------------------------------------- money

def money(cents):
    """Whole cents as a currency string, thousands separated, with a real
    Unicode minus. Formatted exactly as accounting._money formats a balance:
    the two apps are read on the same desk and must not disagree about what
    money looks like."""
    try:
        n = int(cents)
    except (TypeError, ValueError):
        n = 0
    sign = "−" if n < 0 else ""
    n = abs(n)
    return "%s$%s" % (sign, format(n // 100, ",")) + ".%02d" % (n % 100)


def parse_money(text):
    """A typed amount as whole cents, or None when there is no number in it.

    Accepts what a person types off a paper bill: "84.20", "$84.20", "84",
    "1,204.50". Rounds half away from zero on the cent, in integer arithmetic —
    float(text) * 100 rounds 8.4 to 839 cents often enough to matter, and this
    is money."""
    s = "".join(c for c in str(text) if c.isdigit() or c in ".-")
    neg = s.startswith("-")
    s = s.lstrip("-").replace("-", "")
    if not s or s == ".":
        return None
    whole, _dot, frac = s.partition(".")
    frac = (frac.replace(".", "") + "00")[:3]
    try:
        cents = int(whole or 0) * 100 + int(frac[:2] or 0)
    except ValueError:
        return None
    if len(frac) > 2 and frac[2].isdigit() and int(frac[2]) >= 5:
        cents += 1
    cents = min(cents, MAX_CENTS)
    return -cents if neg else cents


# ------------------------------------------------------- forgiving readers
# A store section of the wrong TYPE must cost only itself. The whole file is
# the only copy of these records, so a hand-edited or half-written one has to
# open the app with everything it CAN read rather than stopping it opening.

def _records(v):
    """A store section as a list of records: a list as-is, an object as its
    values in file order, anything else as nothing."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return []


def _clamp_int(value, low, high, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _cents(value):
    """A stored amount as whole cents, or None for a bill with no fixed figure.

    A float in the store is read rather than dropped: an older exporter, or a
    hand edit, can leave 84.2 where 8420 belongs, and refusing it would show a
    bill with no amount at all."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        return max(-MAX_CENTS, min(MAX_CENTS, int(round(value * 100))))
    try:
        return max(-MAX_CENTS, min(MAX_CENTS, int(value)))
    except (TypeError, ValueError):
        return parse_money(value) if isinstance(value, str) else None


def _payment(raw):
    if not isinstance(raw, dict):
        return None
    on = raw.get("on")
    on = on if _parts(on) else today_key()
    due = raw.get("for")
    method = raw.get("method")
    return {"on": on,
            "for": due if _parts(due) else None,
            "amount": _cents(raw.get("amount")),
            "method": method if method in METHODS else "",
            "ref": _text(raw.get("ref"), 80)}


def normalise(raw, index=0, seen=None):
    """One stored record as the bill the app works with, or None when there is
    no bill in it at all. Every field is re-validated rather than trusted."""
    if not isinstance(raw, dict):
        return None
    payee = _text(raw.get("payee"), 80)
    if not payee:
        return None
    bid = raw.get("id")
    if not isinstance(bid, str) or not bid or (seen is not None
                                               and bid in seen):
        bid = "b%d%s" % (index, os.urandom(3).hex())
    if seen is not None:
        seen.add(bid)
    due = raw.get("due")
    method = raw.get("method")
    every = raw.get("every")
    return {
        "id": bid,
        "payee": payee,
        "account": _text(raw.get("account"), 60),
        "amount": _cents(raw.get("amount")),
        # An unreadable due date becomes today rather than dropping the bill:
        # a payee and an amount are still a bill worth keeping, and the date is
        # the one field the screen makes easy to correct.
        "due": due if _parts(due) else today_key(),
        "every": _clamp_int(every, 0, 120, 1) if every is not None else 1,
        "method": method if method in METHODS else "mail",
        "address": _text(raw.get("address")),
        "phone": _text(raw.get("phone"), 40),
        "note": _text(raw.get("note")),
        "lead": _clamp_int(raw.get("lead"), 0, MAX_LEAD, 5),
        "paid": [p for p in (_payment(x) for x in _records(raw.get("paid")))
                 if p is not None],
    }


def read_bills(path=STORE):
    """Every bill in the store, normalised. Never raises: a missing, damaged or
    hand-edited file yields what can be read out of it, which for an unreadable
    one is nothing.

    Shared with widgets.py so the desktop tile and the app parse the same file
    the same way — the tile showing a different amount from the app is the kind
    of disagreement that makes a person stop believing either of them."""
    try:
        import json
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    seen = set()
    src = raw.get("bills") if isinstance(raw, dict) else raw
    out = []
    for i, item in enumerate(_records(src)):
        bill = normalise(item, i, seen)
        if bill is not None:
            out.append(bill)
    return out


# ------------------------------------------------------------ what is due

def occurrences(bill, limit=_MAX_STEPS):
    """The bill's due dates from its anchor forward, lazily.

    A one-off yields exactly one. A repeating bill yields its anchor plus n
    months for n = 0, 1, 2 …, each with the day of the month clamped into the
    month it lands in (see add_months)."""
    anchor = bill.get("due")
    if _parts(anchor) is None:
        return
    every = bill.get("every") or 0
    if every <= 0:
        yield anchor
        return
    for n in range(limit):
        day = add_months(anchor, n * every)
        if day is None:
            return
        yield day


def due_info(bill, today=None):
    """Everything the screen needs to say about when this bill is due.

        due       the outstanding occurrence, or None when there is none left
        days      whole days from today to it (negative = past)
        post_by   the day a cheque has to be posted, or None
        post_days whole days to post_by, or None
        kind      one of: settled overdue today post soon later
        state     the one line that names it
        last      the most recent payment, or None

    The outstanding occurrence is the EARLIEST one no payment is filed against,
    so a bill goes forward on its own as payments are recorded and nothing has
    to be edited each month. A one-off with its payment filed has no
    outstanding occurrence at all, which is `settled` — a different thing from
    a repeating bill that happens to be paid up, whose next occurrence is
    simply further off."""
    today = today or today_key()
    now = _ordinal(today)
    paid_for = {p["for"] for p in bill.get("paid", []) if p.get("for")}
    payments = sorted((p for p in bill.get("paid", [])),
                      key=lambda p: str(p.get("on") or ""))
    last = payments[-1] if payments else None

    due = None
    for day in occurrences(bill):
        if day not in paid_for:
            due = day
            break
    if due is None:
        return {"due": None, "days": None, "post_by": None, "post_days": None,
                "kind": "settled", "state": _t("Paid"), "last": last}

    o = _ordinal(due)
    days = (o - now) if (o is not None and now is not None) else 0
    lead = bill.get("lead") or 0
    post_by = post_days = None
    if bill.get("method") == "mail" and lead > 0:
        post_by = add_days(due, -lead)
        po = _ordinal(post_by) if post_by else None
        post_days = (po - now) if (po is not None and now is not None) else None

    # The nearest deadline names the state, and for a posted cheque that is not
    # always the due date: a bill due in three days with five days to allow in
    # the post is not "due in 3 days", it is already too late to post.
    if days < 0:
        kind, state = "overdue", _t("Overdue")
    elif days == 0:
        kind, state = "today", _t("Due today")
    elif post_days is not None and post_days <= 0:
        kind, state = "post", _t("Post now")
    elif post_days is not None and post_days == 1:
        kind, state = "post", _t("Post tomorrow")
    elif post_days is not None and post_days <= 3:
        kind, state = "post", _t("Post in %d days") % post_days
    elif days == 1:
        kind, state = "soon", _t("Due tomorrow")
    elif days <= SOON_DAYS:
        kind, state = "soon", _t("Due in %d days") % days
    else:
        kind, state = "later", _t("Due %s") % fmt_short(due)
    return {"due": due, "days": days, "post_by": post_by,
            "post_days": post_days, "kind": kind, "state": state, "last": last}


# Which states are asking to be acted on. The sidebar count, the desktop tile
# and the sort order all read this one set, so they can never disagree about
# what "needs paying" means.
ACTIVE_KINDS = ("overdue", "today", "post")


def needs_paying(info):
    return info["kind"] in ACTIVE_KINDS


def sort_key(bill, info):
    """Due order: whatever is outstanding first, soonest first, settled last.
    Ties break on the payee so a list of bills all due on the 1st holds still
    instead of shuffling between refreshes."""
    o = _ordinal(info["due"]) if info["due"] else None
    return (o is None, o if o is not None else 0,
            bill["payee"].casefold(), bill["id"])


def month_total(bills, today=None):
    """What is outstanding between today and the end of today's month, in
    cents, ignoring the bills whose figure varies."""
    today = today or today_key()
    p = _parts(today)
    if p is None:
        return 0
    end = _ordinal("%04d-%02d-%02d" % (p[0], p[1], _month_len(p[0], p[1])))
    now = _ordinal(today)
    total = 0
    for bill in bills:
        info = due_info(bill, today)
        o = _ordinal(info["due"]) if info["due"] else None
        # Anything already outstanding counts, however far back it goes: a bill
        # that went unpaid in June is still money owed this month.
        if o is None or o > end:
            continue
        if o < now and not needs_paying(info):
            continue
        total += bill["amount"] or 0
    return total


# ---------------------------------------------------------------- the app

class Bills(nbapp.AppWindow):
    app_name = "Bill Tracker"
    menus = ("File", "Edit", "Bill", "View")

    # How the list is ordered. "due" is the default because the whole point of
    # the list is what has to be done next.
    SORTS = ("due", "payee", "amount")
    SORT_LABEL = {"due": "By Due Date", "payee": "By Payee",
                  "amount": "By Amount"}

    def __init__(self):
        super().__init__()
        self.bills = self._load()
        self.sel = self.bills[0]["id"] if self.bills else ""
        self.sort = "due"
        self._save_error = ""
        self._flash_id = 0
        self._overlay_layer = None
        self._overlay_holder = None
        self._overlay_card = None
        self._overlay_fit = None
        self._build()
        self._install_css()
        self._refresh()

    # -- store ---------------------------------------------------------------

    def _load(self):
        """Read the store. A file this loader recognises nothing in is moved
        aside BEFORE the app's first save can replace it: valid JSON in a shape
        this app does not know parses perfectly, the app opens empty, and the
        close-time flush then writes that emptiness over the only copy of the
        user's bills. nbapp's single .bak cannot cover it — see
        nbapp.quarantine_unrecognized."""
        try:
            import json
            with open(STORE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return []              # missing / unreadable: an empty bill list
        seen = set()
        src = raw.get("bills") if isinstance(raw, dict) else raw
        out = []
        for i, item in enumerate(_records(src)):
            bill = normalise(item, i, seen)
            if bill is not None:
                out.append(bill)
        if not out and not (isinstance(raw, dict)
                            and raw.get("bills") in ([], {})):
            nbapp.quarantine_unrecognized(STORE)
        return out

    def _save(self):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            nbapp.atomic_write_json(STORE, {"bills": self.bills}, indent=1)
            self._save_error = ""
        except OSError as exc:
            # A read-only or full disk must not stop the app working, and must
            # not be silent either: without this, a failed write looks exactly
            # like the app forgetting a bill, because the store keeps whatever
            # the last successful write left in it. Held rather than flashed —
            # the status strip is rewritten on every refresh, so a one-shot
            # message would be wiped a moment later.
            self._save_error = nbapp.save_failure_reason(exc, STORE)

    # -- model helpers -------------------------------------------------------

    def _bill(self, bid=None):
        bid = self.sel if bid is None else bid
        for b in self.bills:
            if b["id"] == bid:
                return b
        return None

    def _ordered(self):
        """(bill, info) pairs in the order the sidebar lists them."""
        pairs = [(b, due_info(b)) for b in self.bills]
        if self.sort == "payee":
            pairs.sort(key=lambda pi: (pi[0]["payee"].casefold(),
                                       pi[0]["id"]))
        elif self.sort == "amount":
            # Largest first, and the bills with no fixed figure after them —
            # sorting an unknown amount as zero buries it under every $1 bill.
            pairs.sort(key=lambda pi: (pi[0]["amount"] is None,
                                       -(pi[0]["amount"] or 0),
                                       pi[0]["payee"].casefold()))
        else:
            pairs.sort(key=lambda pi: sort_key(pi[0], pi[1]))
        return pairs

    # -- css -----------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app, and the app then renders in stock GTK grey.
        css = b"""
        .bl-side { background: #EFEBE0; border-right: 1px solid #C9C4B6; }
        .bl-side *, .bl-main * {
                     font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .bl-eyebrow { font-size: 11px; letter-spacing: 0.14em;
                      font-weight: 700; color: #9A9484; }
        .bl-sidehead { padding: 20px 16px 10px 16px; }
        .bl-new { min-width: 30px; min-height: 30px; padding: 0 6px;
                  border-radius: 8px; background: transparent; border: none;
                  box-shadow: none; }
        .bl-new:hover { background: #EAE3D2; }

        .bl-row { padding: 10px 14px; border-bottom: 1px solid #D7D2C5;
                  background: transparent; }
        .bl-row:hover { background: #F0EADC; }
        /* SELECTED IS MARKED IN INK, NOT IN THE ACCENT, and this is the one
           screen in the OS where that is right. A 3px accent edge means
           "selected" nearly everywhere else (Tasks, Academics, Journal,
           Cookbook, Contacts, Packages, Music). But on a screen about
           deadlines the accent already means THIS ONE NEEDS PAYING, and it is
           carried by the row's own state line and dot. One colour saying both
           things put a red mark on a settled bill purely because the pointer
           had landed on it. Same reasoning, and the same resolution, as
           workout.py's ink marker for today. */
        .bl-row.sel { background: #EAE3D2;
                      box-shadow: inset 3px 0 0 #1A1916; }
        .bl-rowname { font-size: 15px; color: #1A1916; }
        .bl-row.sel .bl-rowname { font-weight: 700; }
        .bl-rowamt { font-size: 14px; color: #1A1916; }
        .bl-rowstate { font-size: 12px; color: #8A857A; }
        .bl-rowstate.hot { color: #C8341E; font-weight: 700; }
        .bl-listempty { font-size: 13px; color: #8A857A; padding: 22px 16px; }
        /* The same absence stated inside the detail column, where the rail's
           16px indent would push it out of line with the section above it. */
        .bl-noneyet { font-size: 13px; color: #8A857A; padding: 2px 0 10px 0; }

        .bl-sidefoot { border-top: 1px solid #C9C4B6; padding: 14px 16px; }
        .bl-footnum { font-size: 20px; color: #1A1916; }
        .bl-footnum.hot { color: #C8341E; }
        .bl-footlabel { font-size: 11px; letter-spacing: 0.12em;
                        font-weight: 700; color: #9A9484; }

        .bl-main { background: #FCFBF8; }
        .bl-payee { font-size: 30px; font-weight: 700; color: #1A1916; }
        .bl-sub { font-size: 14px; color: #6E695E; }
        .bl-amount { font-size: 30px; font-weight: 700; color: #1A1916; }
        .bl-amount.varies { font-size: 20px; color: #6E695E;
                            font-weight: 400; }
        .bl-rule { background: #D7D2C5; }

        /* The one line that says what to do next. A quiet band by default; the
           accent edge and ink type only when something is actually owed, so
           the colour keeps meaning "this one, now". */
        .bl-act { background: #F4F2EC; border: 1px solid #D7D2C5;
                  border-radius: 12px; padding: 16px 18px; }
        .bl-act.hot { background: #FBEFEC; border-color: #E4C7C0;
                      box-shadow: inset 3px 0 0 #C8341E; }
        .bl-actline { font-size: 17px; font-weight: 700; color: #1A1916; }
        .bl-act.hot .bl-actline { color: #C8341E; }
        .bl-actsub { font-size: 13px; color: #6E695E; }

        /* The primary action is an ink slab, the OS-wide primary. The label
           node has to be named as well: the theme's `* { color: ink }` matches
           a button's own label, so a colour set on the button never reaches
           its text and the slab came out ink-on-ink. */
        .bl-primary { background: #1A1916; background-image: none;
                      border: 1px solid #1A1916; border-radius: 8px;
                      padding: 9px 20px; font-size: 14px; box-shadow: none;
                      color: #FCFBF8; }
        .bl-primary label { color: #FCFBF8; font-weight: 700; }
        .bl-primary:hover { background: #3A362E; border-color: #3A362E; }
        .bl-quiet { background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; padding: 9px 18px; font-size: 14px;
                    box-shadow: none; color: #3A362E; }
        .bl-quiet label { color: #3A362E; }
        .bl-quiet:hover { background: #F1EEE6; }

        /* The remittance address, set as an address: a bordered panel the
           width of an envelope's address panel, lines close together, nothing
           else inside it. */
        .bl-addr { background: #F8F7F2; border: 1px solid #C9C4B6;
                   border-radius: 12px; padding: 20px 22px; }
        .bl-addrline { font-size: 17px; color: #1A1916; }
        .bl-addrempty { font-size: 14px; color: #9A9484; }
        .bl-phone { font-size: 24px; font-weight: 700; color: #1A1916; }

        .bl-factlabel { font-size: 11px; letter-spacing: 0.12em;
                        font-weight: 700; color: #9A9484; }
        .bl-factval { font-size: 16px; color: #1A1916; }
        .bl-factval.hot { color: #C8341E; font-weight: 700; }
        .bl-factval.quiet { color: #9A9484; }

        .bl-payhead { border-bottom: 1px solid #C9C4B6; padding-bottom: 6px; }
        .bl-payrow { padding: 9px 0; border-bottom: 1px solid #EFEBE0; }
        .bl-paycell { font-size: 14px; color: #2A2620; }
        .bl-paycell.quiet { color: #8A857A; }
        .bl-empty-title { font-size: 17px; color: #1A1916; }
        .bl-empty-body { font-size: 14px; color: #6E695E; }
        .bl-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }

        /* Overlay cards. There is no compositor on this stack, so a real
           Gtk.Dialog is a second toplevel the window manager may stack behind
           the app; every modal in this OS is drawn INSIDE the window instead. */
        .bl-scrim { background: rgba(26, 25, 22, 0.28); }
        .bl-card { background: #FCFBF8; border: 1px solid #1A1916;
                   border-radius: 12px; padding: 24px 26px 20px 26px; }
        .bl-cardtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .bl-cardmsg { font-size: 13px; color: #6E695E; }
        .bl-flabel { font-size: 12px; letter-spacing: 0.06em; color: #6E695E; }
        .bl-ferr { font-size: 12px; color: #C8341E; }
        .bl-card entry, .bl-card spinbutton {
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; box-shadow: none; padding: 5px 9px;
                   font-size: 14px; color: #1A1916; }
        /* A combo is left ENTIRELY to the Papertone theme, which draws it as a
           raised, tinted button. Restyling it here to match the paper entries
           above made it look like one more text field: this OS ships no
           symbolic icon theme, so GTK's drop-down arrow resolves to nothing in
           every combo in every app, and the raised button IS the affordance
           that is left. Matching the entries threw that away. */
        /* GTK draws NO border from a GtkTextView's own CSS, so the address box
           was invisible: a label, then blank paper with typing in it. The
           outline goes on a plain Box wrapped round it, which is the same
           thing contacts.py does for its notes field. */
        .bl-addrframe { background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; }
        .bl-addredit, .bl-addredit text { background: #FCFBF8;
                   font-size: 14px; color: #1A1916; }
        /* A segmented picker: four buttons that read as one control. */
        .bl-seg { background: #FCFBF8; border: 1px solid #C9C4B6;
                  border-radius: 8px; padding: 7px 10px; font-size: 13px;
                  box-shadow: none; color: #3A362E; }
        .bl-seg label { color: #3A362E; }
        .bl-seg:hover { background: #F1EEE6; }
        .bl-seg.on { background: #1A1916; border-color: #1A1916; }
        .bl-seg.on label { color: #FCFBF8; font-weight: 700; }
        .bl-danger { background: #C8341E; background-image: none;
                     border: 1px solid #C8341E; border-radius: 8px;
                     padding: 9px 20px; font-size: 14px; box-shadow: none; }
        .bl-danger label { color: #FCFBF8; font-weight: 700; }
        .bl-danger:hover { background: #B12D19; border-color: #B12D19; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                       # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    # -- chrome --------------------------------------------------------------

    def _build(self):
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main.pack_start(self._sidebar(), False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right.get_style_context().add_class("bl-main")
        self._detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._detail)
        right.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("bl-status")
        # A fixed bottom strip must be pinned, or GTK3 propagates vexpand up
        # from the content above and the strip floats mid-window.
        self.status.set_vexpand(False)
        right.pack_start(self.status, False, False, 0)
        main.pack_start(right, True, True, 0)

        self.content.pack_start(main, True, True, 0)

    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.get_style_context().add_class("bl-side")
        side.set_size_request(SIDEBAR_W, -1)

        head = Gtk.Box(spacing=8)
        head.get_style_context().add_class("bl-sidehead")
        self._sidetitle = Gtk.Label(label=_t("BILLS"), xalign=0)
        self._sidetitle.get_style_context().add_class("bl-eyebrow")
        head.pack_start(self._sidetitle, True, True, 0)
        add = Gtk.Button()
        add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("bl-new")
        add.set_tooltip_text(_t("Add a bill"))
        nbapp.name_control(add, _t("Add a bill"))
        add.add(nbicons.image("plus", 16, "#3A362E"))
        add.connect("clicked", lambda *_: self._open_form(None))
        head.pack_end(add, False, False, 0)
        side.pack_start(head, False, False, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._list)
        side.pack_start(scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        foot.get_style_context().add_class("bl-sidefoot")
        self._foot_total, blk = self._foot_block(_t("DUE THIS MONTH"))
        foot.pack_start(blk, False, False, 0)
        self._foot_need, blk = self._foot_block(_t("NEEDS PAYING"))
        foot.pack_start(blk, False, False, 0)
        side.pack_start(foot, False, False, 0)
        return side

    @staticmethod
    def _foot_block(caption):
        """(number label, block) — a figure over its caption, the shape the
        Workout app's sidebar footer already uses."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        num = Gtk.Label(xalign=0)
        num.get_style_context().add_class("bl-footnum")
        num.set_ellipsize(Pango.EllipsizeMode.END)
        num.set_max_width_chars(1)
        box.pack_start(num, False, False, 0)
        cap = Gtk.Label(label=caption, xalign=0)
        cap.get_style_context().add_class("bl-footlabel")
        # The caption WRAPS; only the figure above it ellipsizes. They are
        # different kinds of text: the figure is data of unbounded length (a
        # currency total) and must never set the window width, but the caption
        # is an authored UI string whose whole job is to say which figure this
        # is. Cutting it lost exactly that. In Greek the two captions are
        # ΠΡΟΣ ΠΛΗΡΩΜΗ ΑΥΤΟΝ ΤΟΝ ΜΗΝΑ ("DUE THIS MONTH") and ΠΡΟΣ ΠΛΗΡΩΜΗ
        # ("NEEDS PAYING") — the second is a strict PREFIX of the first, so
        # ellipsis at the 219px sidebar width rendered both as "ΠΡΟΣ ΠΛΗΡΩΜΗ…"
        # and the two totals became one label printed twice.
        # WORD_CHAR, not WORD: it lets a single long word break, which keeps the
        # label's MINIMUM width near zero, so wrapping cannot push the app's
        # minimum out the way an unellipsized label would (tools/minsize_sweep).
        cap.set_line_wrap(True)
        cap.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        cap.set_max_width_chars(1)
        box.pack_start(cap, False, False, 0)
        return num, box

    # -- refresh -------------------------------------------------------------

    def _refresh(self):
        self._fill_list()
        self._fill_detail()
        self._refresh_footer()
        self._refresh_status()

    def _fill_list(self):
        for child in self._list.get_children():
            self._list.remove(child)
        pairs = self._ordered()
        if not pairs:
            empty = Gtk.Label(label=_t("No bills"), xalign=0)
            empty.get_style_context().add_class("bl-listempty")
            self._list.pack_start(empty, False, False, 0)
            self._list.show_all()
            return
        if not self._bill():
            self.sel = pairs[0][0]["id"]
        for bill, info in pairs:
            self._list.pack_start(self._row(bill, info), False, False, 0)
        self._list.show_all()

    def _row(self, bill, info):
        """One sidebar row: the payee on its own line, then what has to happen
        and what it costs on the line under it.

        The amount sat BESIDE the payee at first and took a fixed column out of
        a 252px rail, so "Meridian Water District" was clipped to "Meridian
        Water Dis…" with an inch of empty rail beside it. A payee is the thing
        being looked for in this list; it gets the whole width."""
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ctx = row.get_style_context()
        ctx.add_class("bl-row")
        if bill["id"] == self.sel:
            ctx.add_class("sel")

        name = Gtk.Label(label=bill["payee"], xalign=0)
        name.get_style_context().add_class("bl-rowname")
        # An ellipsizing label still reports its WHOLE string as its natural
        # width, and this rail is a fixed 252px: one long payee stretched the
        # sidebar and took the width out of the detail pane beside it.
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)
        row.pack_start(name, False, False, 0)

        under = Gtk.Box(spacing=8)
        state = Gtk.Label(label=info["state"], xalign=0)
        sctx = state.get_style_context()
        sctx.add_class("bl-rowstate")
        if needs_paying(info):
            sctx.add_class("hot")
        state.set_ellipsize(Pango.EllipsizeMode.END)
        state.set_max_width_chars(1)
        under.pack_start(state, True, True, 0)
        amt = Gtk.Label(label=(money(bill["amount"]) if bill["amount"]
                               is not None else _t("Varies")), xalign=1)
        amt.get_style_context().add_class("bl-rowamt")
        under.pack_end(amt, False, False, 0)
        row.pack_start(under, False, False, 0)

        # A real Gtk.Button, not an EventBox: it takes the keyboard as well as
        # the pointer, carries the focus ring the rest of the OS uses, and tells
        # assistive technology this row is a control rather than decoration.
        hit = Gtk.Button()
        hit.set_relief(Gtk.ReliefStyle.NONE)
        hit.get_style_context().add_class("bl-rowhit")
        hit.add(row)
        hit.set_tooltip_text("%s  %s" % (bill["payee"], info["state"]))
        nbapp.name_control(hit, bill["payee"])
        hit.connect("clicked", self._on_row_clicked, bill["id"])
        return hit

    def _on_row_clicked(self, _w, bid):
        if bid == self.sel:
            return
        self.sel = bid
        self._refresh()

    def _refresh_footer(self):
        total = month_total(self.bills)
        self._foot_total.set_text(money(total))
        n = sum(1 for b in self.bills if needs_paying(due_info(b)))
        self._foot_need.set_text("%d" % n)
        ctx = self._foot_need.get_style_context()
        if n:
            ctx.add_class("hot")
        else:
            ctx.remove_class("hot")
        self._sidetitle.set_text(_t("BILLS") if not self.bills
                                 else "%s  %d" % (_t("BILLS"),
                                                  len(self.bills)))

    def _refresh_status(self):
        if self._save_error:
            self.status.set_text(self._save_error)
            return
        if not self.bills:
            self.status.set_text(_t("No bills"))
            return
        n = sum(1 for b in self.bills if needs_paying(due_info(b)))
        left = _t("%d bills") % len(self.bills) if len(self.bills) != 1 \
            else _t("1 bill")
        right = (_t("%d need paying") % n if n != 1 else _t("1 needs paying")) \
            if n else _t("Nothing due")
        # The separator is composed OUTSIDE the catalog keys: a key with
        # padding on it ("  ·  finishes…") matches nothing and shows English.
        self.status.set_text(left + "  ·  " + right)

    def _flash(self, message):
        """Say what just happened, then go back to the standing summary. The
        strip is rewritten on every refresh, so the message is held by a token
        that a later refresh invalidates rather than by a widget flag."""
        self._flash_id += 1
        token = self._flash_id
        self.status.set_text(message)

        def _back():
            if token == self._flash_id:
                self._refresh_status()
            return False
        GLib.timeout_add_seconds(4, _back)

    # -- the detail pane -----------------------------------------------------

    def _fill_detail(self):
        for child in self._detail.get_children():
            self._detail.remove(child)
        bill = self._bill()
        if bill is None:
            self._detail.pack_start(self._empty_state(), True, True, 0)
            self._detail.show_all()
            return
        info = due_info(bill)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.set_halign(Gtk.Align.START)
        sw, _sh = nbapp.screen_size()
        # 112 covers the column's own 40px margins on each side plus the
        # vertical scrollbar this pane grows one of. At 96 the window's NATURAL
        # width came out 4px past a 1024 panel, which GTK resolves by squeezing
        # something else on the row.
        col.set_size_request(max(430, min(COLUMN_W, sw - SIDEBAR_W - 112)), -1)
        col.set_margin_start(40)
        col.set_margin_end(40)
        col.set_margin_top(34)
        col.set_margin_bottom(34)

        col.pack_start(self._head_block(bill, info), False, False, 0)
        col.pack_start(self._rule(22, 20), False, False, 0)
        col.pack_start(self._action_block(bill, info), False, False, 0)
        col.pack_start(self._how_block(bill, info), False, False, 0)
        col.pack_start(self._payments_block(bill), False, False, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(col, False, False, 0)
        self._detail.pack_start(holder, False, False, 0)
        self._detail.show_all()

    @staticmethod
    def _rule(top=0, bottom=0):
        rule = Gtk.Box()
        rule.get_style_context().add_class("bl-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(top)
        rule.set_margin_bottom(bottom)
        return rule

    @staticmethod
    def _eyebrow(text, top=0, bottom=8):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("bl-eyebrow")
        lbl.set_margin_top(top)
        lbl.set_margin_bottom(bottom)
        return lbl

    def _head_block(self, bill, info):
        head = Gtk.Box(spacing=24)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        payee = Gtk.Label(label=bill["payee"], xalign=0)
        payee.get_style_context().add_class("bl-payee")
        payee.set_ellipsize(Pango.EllipsizeMode.END)
        payee.set_max_width_chars(1)
        left.pack_start(payee, False, False, 0)
        sub = Gtk.Label(label=(_t("Account %s") % bill["account"]
                               if bill["account"]
                               else _t(METHOD_LABEL[bill["method"]])), xalign=0)
        sub.get_style_context().add_class("bl-sub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(1)
        left.pack_start(sub, False, False, 0)
        head.pack_start(left, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        right.set_valign(Gtk.Align.START)
        amt = Gtk.Label(label=(money(bill["amount"]) if bill["amount"]
                               is not None else _t("Amount varies")), xalign=1)
        actx = amt.get_style_context()
        actx.add_class("bl-amount")
        if bill["amount"] is None:
            actx.add_class("varies")
        right.pack_start(amt, False, False, 0)
        when = Gtk.Label(label=(_t("DUE %s") % fmt_due(info["due"]).upper()
                                if info["due"] else _t("SETTLED")), xalign=1)
        when.get_style_context().add_class("bl-eyebrow")
        right.pack_end(when, False, False, 0)
        head.pack_end(right, False, False, 0)
        return head

    def _action_block(self, bill, info):
        """The band that names the one thing to do, and the buttons that do it.

        Everything else on the screen is reference. This is the answer to the
        question the app is opened with, so it is the only element that ever
        carries the accent."""
        band = Gtk.Box(spacing=18)
        ctx = band.get_style_context()
        ctx.add_class("bl-act")
        if needs_paying(info):
            ctx.add_class("hot")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text.set_valign(Gtk.Align.CENTER)
        line = Gtk.Label(label=self._headline(bill, info), xalign=0)
        line.get_style_context().add_class("bl-actline")
        line.set_ellipsize(Pango.EllipsizeMode.END)
        line.set_max_width_chars(1)
        text.pack_start(line, False, False, 0)
        sub = self._subline(bill, info)
        if sub:
            lbl = Gtk.Label(label=sub, xalign=0)
            lbl.get_style_context().add_class("bl-actsub")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(1)
            text.pack_start(lbl, False, False, 0)
        band.pack_start(text, True, True, 0)

        buttons = Gtk.Box(spacing=10)
        buttons.set_valign(Gtk.Align.CENTER)
        pay = Gtk.Button(label=_t("Record Payment"))
        pay.set_relief(Gtk.ReliefStyle.NONE)
        pay.get_style_context().add_class("bl-primary")
        pay.connect("clicked", lambda *_: self._open_payment())
        buttons.pack_start(pay, False, False, 0)
        edit = Gtk.Button(label=_t("Edit"))
        edit.set_relief(Gtk.ReliefStyle.NONE)
        edit.get_style_context().add_class("bl-quiet")
        edit.connect("clicked", lambda *_: self._open_form(self._bill()))
        buttons.pack_start(edit, False, False, 0)
        band.pack_end(buttons, False, False, 0)
        return band

    @staticmethod
    def _headline(bill, info):
        """The action band's first line: the deadline, said plainly.

        Longer than `info["state"]`, which is written for a 252px rail and a
        desktop tile. Here there is room to count the days, and the days are
        what a person actually wants off this line — the DATE is already in the
        header above it, so repeating it would have made the band decorative."""
        if info["kind"] == "settled":
            return _t("Paid")
        if info["kind"] == "overdue":
            n = -info["days"]
            return _t("Overdue by 1 day") if n == 1 \
                else _t("Overdue by %d days") % n
        if info["kind"] == "later":
            return _t("Due in %d days") % info["days"]
        return info["state"]

    @staticmethod
    def _subline(bill, info):
        """Its second line: strictly what the first line did NOT say.

        Every branch here adds a date the headline does not carry. An earlier
        version appended the due date unconditionally, so a bill six weeks off
        read "Due 28 Aug" over "Due 28 August 2026" — the same fact twice, in
        two different formats, which reads as a layout accident."""
        if info["kind"] == "post":
            # The headline named the POSTAL deadline; the due date is a
            # different, later day and is the one being worked back from.
            return _t("Due %s") % fmt_due(info["due"])
        if info["kind"] == "settled":
            # A payment's date always carries its year: the history is the one
            # place on this screen that looks backwards, and "Paid 1 August"
            # does not say which August.
            on = fmt_date(info["last"]["on"], year=True) if info["last"] else ""
            return _t("Paid %s") % on if on else ""
        if info["post_by"]:
            return _t("Post by %s") % fmt_date(info["post_by"])
        return ""

    def _how_block(self, bill, info):
        """The instructions: the address panel or the phone number on the left,
        the facts that go with it on the right."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.pack_start(self._eyebrow(_t(METHOD_HEAD[bill["method"]]), 30, 10),
                       False, False, 0)

        cols = Gtk.Box(spacing=30)
        panel = self._method_panel(bill)
        panel.set_size_request(ADDRESS_W, -1)
        panel.set_valign(Gtk.Align.START)
        cols.pack_start(panel, False, False, 0)

        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        facts.set_valign(Gtk.Align.START)
        for label, value, tone in self._facts(bill, info):
            facts.pack_start(self._fact(label, value, tone), False, False, 0)
        cols.pack_start(facts, True, True, 0)
        box.pack_start(cols, False, False, 0)
        return box

    def _method_panel(self, bill):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        panel.get_style_context().add_class("bl-addr")
        if bill["method"] == "mail":
            lines = [ln.strip() for ln in bill["address"].splitlines()
                     if ln.strip()]
            if not lines:
                panel.pack_start(self._addr_empty(_t("No address")),
                                 False, False, 0)
            for line in lines:
                lbl = Gtk.Label(label=line, xalign=0)
                lbl.get_style_context().add_class("bl-addrline")
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_max_width_chars(1)
                panel.pack_start(lbl, False, False, 0)
        elif bill["method"] == "phone":
            if bill["phone"]:
                num = Gtk.Label(label=bill["phone"], xalign=0)
                num.get_style_context().add_class("bl-phone")
                num.set_ellipsize(Pango.EllipsizeMode.END)
                num.set_max_width_chars(1)
                panel.pack_start(num, False, False, 0)
            else:
                panel.pack_start(self._addr_empty(_t("No phone number")),
                                 False, False, 0)
        else:
            if bill["note"]:
                for line in bill["note"].splitlines() or [""]:
                    lbl = Gtk.Label(label=line, xalign=0)
                    lbl.get_style_context().add_class("bl-addrline")
                    lbl.set_line_wrap(True)
                    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    lbl.set_max_width_chars(1)
                    panel.pack_start(lbl, False, False, 0)
            else:
                panel.pack_start(self._addr_empty(_t("No notes")),
                                 False, False, 0)
        return panel

    @staticmethod
    def _addr_empty(text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("bl-addrempty")
        return lbl

    def _facts(self, bill, info):
        """(label, value, tone) for the column beside the panel. `tone` is
        "hot" for the postal deadline once it has arrived, "quiet" for a field
        the bill has not been given."""
        out = []
        out.append((_t("ACCOUNT NUMBER"), bill["account"] or _t("Not set"),
                    "" if bill["account"] else "quiet"))
        # Labelled as the form labels the field that fills it. It said
        # "BEFORE THE NUMBER ANSWERS" here and "Notes" in the sheet, which
        # leaves a person hunting for the field that wrote the line they can
        # see.
        if bill["method"] == "phone" and bill["note"]:
            out.append((_t("NOTES"), bill["note"], ""))
        if bill["method"] == "mail":
            if info["post_by"]:
                hot = info["post_days"] is not None and info["post_days"] <= 0
                out.append((_t("POST BY"), fmt_date(info["post_by"]),
                            "hot" if hot else ""))
                out.append((_t("DAYS IN THE POST"), "%d" % bill["lead"], ""))
            else:
                out.append((_t("DAYS IN THE POST"), _t("None"), "quiet"))
        if bill["method"] == "mail" and bill["phone"]:
            out.append((_t("PHONE"), bill["phone"], ""))
        out.append((_t("REPEATS"),
                    _t(REPEAT_LABEL.get(bill["every"], "Every month"))
                    if bill["every"] in REPEAT_LABEL
                    else _t("Every %d months") % bill["every"], ""))
        return out

    @staticmethod
    def _fact(label, value, tone=""):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("bl-factlabel")
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(1)
        box.pack_start(lbl, False, False, 0)
        val = Gtk.Label(label=value, xalign=0)
        vctx = val.get_style_context()
        vctx.add_class("bl-factval")
        if tone:
            vctx.add_class(tone)
        val.set_line_wrap(True)
        val.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        val.set_max_width_chars(1)
        box.pack_start(val, False, False, 0)
        return box

    def _payments_block(self, bill):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # 14px under the section name, not 0: the column headings beneath are
        # also small caps in the same grey, and with the two rows touching they
        # read as one heading that had gone wrong rather than as a section over
        # a table.
        box.pack_start(self._eyebrow(_t("PAYMENTS"), 34, 14), False, False, 0)
        rows = sorted(bill["paid"], key=lambda p: str(p.get("on") or ""),
                      reverse=True)
        if not rows:
            empty = Gtk.Label(label=_t("No payments recorded"), xalign=0)
            empty.get_style_context().add_class("bl-noneyet")
            box.pack_start(empty, False, False, 0)
            return box
        head = self._pay_row((_t("DATE"), _t("AMOUNT"), _t("HOW"),
                              _t("REFERENCE")), head=True)
        box.pack_start(head, False, False, 0)
        for p in rows:
            box.pack_start(self._pay_row((
                fmt_date(p["on"], year=True),
                money(p["amount"]) if p["amount"] is not None else "",
                _t(METHOD_LABEL[p["method"]]) if p["method"] in METHOD_LABEL
                else "",
                p["ref"] or "")), False, False, 0)
        return box

    def _pay_row(self, cells, head=False):
        row = Gtk.Box(spacing=26)
        ctx = row.get_style_context()
        ctx.add_class("bl-payhead" if head else "bl-payrow")
        widths = (150, 110, 110, -1)
        for i, text in enumerate(cells):
            lbl = Gtk.Label(label=text, xalign=1 if i == 1 else 0)
            lctx = lbl.get_style_context()
            if head:
                lctx.add_class("bl-factlabel")
            else:
                lctx.add_class("bl-paycell")
                if i >= 2:
                    lctx.add_class("quiet")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(1)
            if widths[i] > 0:
                lbl.set_size_request(widths[i], -1)
                row.pack_start(lbl, False, False, 0)
            else:
                row.pack_start(lbl, True, True, 0)
        return row

    def _empty_state(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        icon = nbicons.image("bills", 34, "#9A9484")
        icon.set_margin_bottom(6)
        box.pack_start(icon, False, False, 0)
        title = Gtk.Label(label=_t("No bills"))
        title.get_style_context().add_class("bl-empty-title")
        box.pack_start(title, False, False, 0)
        body = Gtk.Label(label=_t("A bill holds the amount, the due date and "
                                  "how it is paid."))
        body.get_style_context().add_class("bl-empty-body")
        box.pack_start(body, False, False, 0)
        btn = Gtk.Button(label=_t("Add a bill"))
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("bl-quiet")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(8)
        btn.connect("clicked", lambda *_: self._open_form(None))
        box.pack_start(btn, False, False, 0)
        return box

    # -- overlay plumbing ----------------------------------------------------
    #
    # One layer at a time, drawn inside the app window. There is no compositor
    # here, so a real Gtk.Dialog is a second toplevel the window manager may
    # stack behind the app — which reads as a control that did nothing.

    def _overlay_size(self):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w > 1 and h > 1:
            return w, h
        # Never a hardcoded 1920x1080: on a smaller panel that centres the card
        # off the bottom-right corner of the screen.
        return nbapp.screen_size()

    def _show_overlay(self, card, prepare=None):
        """Put `card` up over the window, centred, behind a scrim.

        `prepare` runs after the layer is shown and BEFORE the card is
        measured. The bill sheet hides the fields its chosen method does not
        use, and hiding them after the measure left the card the height it
        would have been with all of them on it — a sheet with a hand's width of
        blank paper in the middle of it."""
        self._close_overlay()
        layer = Gtk.Fixed()
        w, h = self._overlay_size()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("bl-scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(w, h)
        scrim.connect("button-press-event",
                      lambda *_a: (self._close_overlay(), True)[1])
        layer.put(scrim, 0, 0)

        # The card is as tall as it needs to be, UP TO the window. A sheet with
        # ten fields on it fits a 1080 panel and does not fit a 740 one, and a
        # card taller than the screen loses its buttons off the bottom edge
        # with no way to reach them. This costs nothing when it fits: the
        # scroller is given the card's own height and shows no bar.
        fit = Gtk.ScrolledWindow()
        fit.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        fit.add(card)

        holder = Gtk.EventBox()      # its own GdkWindow, so the card blits
        holder.add(fit)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        self._overlay_layer = layer
        self._overlay_holder = holder
        self._overlay_card = card
        self._overlay_fit = fit
        layer.show_all()
        if prepare is not None:
            prepare()
        self._centre_overlay()
        try:
            win = layer.get_window()
            if win is not None:
                win.raise_()
        except Exception:                                       # noqa: BLE001
            pass
        return layer

    def _centre_overlay(self):
        """Measure the card as it is NOW and put it in the middle.

        Called again whenever the card's contents change size — switching the
        payment method swaps three fields for one, and a card left at its old
        size either clips the address box or floats over a strip of nothing.

        The CARD is measured, and the scroller around it is then given that
        size (capped at the window). Asking the SCROLLER how big it wants to be
        does not work: it is happy at its minimum, so the sheet collapsed to a
        60px strip of its own title bar at the top of the screen."""
        layer, holder = self._overlay_layer, self._overlay_holder
        card, fit = self._overlay_card, self._overlay_fit
        if layer is None or holder is None or card is None or fit is None:
            return
        w, h = self._overlay_size()
        req = card.get_preferred_size()[1]
        cw = min(req.width, max(280, w - 48))
        ch = min(req.height, max(240, h - 48))
        fit.set_size_request(cw, ch)
        layer.move(holder, max(0, (w - cw) // 2), max(0, (h - ch) // 2))

    def _close_overlay(self):
        """Take the top overlay down. True when there was one, so Esc can be
        chained through the overlays before it reaches the app."""
        layer = self._overlay_layer
        if layer is None:
            return False
        self._overlay_layer = None
        self._overlay_holder = None
        self._overlay_card = None
        self._overlay_fit = None
        try:
            self._overlay.remove(layer)
            layer.destroy()
        except Exception:                                       # noqa: BLE001
            pass
        return True

    @staticmethod
    def _card(title, note=""):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("bl-card")
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("bl-cardtitle")
        card.pack_start(head, False, False, 0)
        if note:
            msg = Gtk.Label(label=note, xalign=0)
            msg.get_style_context().add_class("bl-cardmsg")
            msg.set_line_wrap(True)
            msg.set_max_width_chars(46)
            card.pack_start(msg, False, False, 0)
        return card

    @staticmethod
    def _field(label, widget, width=150):
        row = Gtk.Box(spacing=14)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("bl-flabel")
        lbl.set_size_request(width, -1)
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(1)
        row.pack_start(lbl, False, False, 0)
        row.pack_start(widget, True, True, 0)
        return row

    def _date_row(self, label, day):
        """A day/month/year picker. Three controls, not a typed "YYYY-MM-DD":
        a date entered as text is a date that can be entered wrongly, and the
        one field a bill cannot afford to have wrong is when it is due."""
        p = _parts(day) or _parts(today_key())
        box = Gtk.Box(spacing=8)
        d = Gtk.SpinButton.new_with_range(1, 31, 1)
        d.set_value(p[2])
        d.set_size_request(64, -1)
        m = Gtk.ComboBoxText()
        for name in MONTHS:
            m.append_text(_t(name))
        m.set_active(p[1] - 1)
        y = Gtk.SpinButton.new_with_range(1970, 2999, 1)
        y.set_value(p[0])
        y.set_numeric(True)
        y.set_size_request(88, -1)
        box.pack_start(d, False, False, 0)
        box.pack_start(m, False, False, 0)
        box.pack_start(y, False, False, 0)
        return self._field(label, box), (d, m, y)

    @staticmethod
    def _date_of(parts):
        """The three date controls read back as "YYYY-MM-DD", with the day
        clamped into the month. get_active(), never get_active_text(): nbi18n
        translates a combo's items, so the text read back is the TRANSLATION
        and would never match an English month name."""
        d, m, y = parts
        year = int(y.get_value())
        month = max(1, min(12, m.get_active() + 1))
        day = max(1, min(int(d.get_value()), _month_len(year, month)))
        return "%04d-%02d-%02d" % (year, month, day)

    # -- add / edit a bill ---------------------------------------------------

    def _open_form(self, bill):
        """The bill sheet. `bill` is None to add one, or the bill to edit."""
        self._close_menu()
        editing = bill is not None
        card = self._card(_t("Edit Bill") if editing else _t("Add a Bill"))
        card.set_size_request(560, -1)

        payee = Gtk.Entry()
        payee.set_text(bill["payee"] if editing else "")
        payee.set_placeholder_text(_t("Who the money goes to"))
        card.pack_start(self._field(_t("Payee"), payee), False, False, 0)

        account = Gtk.Entry()
        account.set_text(bill["account"] if editing else "")
        card.pack_start(self._field(_t("Account number"), account),
                        False, False, 0)

        amount = Gtk.Entry()
        amount.set_text(money(bill["amount"]).replace("$", "")
                        if editing and bill["amount"] is not None else "")
        amount.set_placeholder_text(_t("Leave empty if it varies"))
        card.pack_start(self._field(_t("Amount"), amount), False, False, 0)

        row, date_parts = self._date_row(
            _t("Next due"), bill["due"] if editing else today_key())
        card.pack_start(row, False, False, 0)

        repeat = Gtk.ComboBoxText()
        for months in REPEATS:
            repeat.append_text(_t(REPEAT_LABEL[months]))
        cur = bill["every"] if editing else 1
        repeat.set_active(REPEATS.index(cur) if cur in REPEATS else 0)
        card.pack_start(self._field(_t("Repeats"), repeat), False, False, 0)

        card.pack_start(self._rule(6, 6), False, False, 0)

        # The method picker, and the fields that belong to whichever method is
        # chosen. Only one method's fields are ever on screen: a form showing a
        # postal address AND a phone number asks which one the bill uses and
        # then does not say.
        method = {"id": bill["method"] if editing else "mail"}
        segs = {}
        seg_box = Gtk.Box(spacing=8)
        for mid in METHODS:
            b = Gtk.Button(label=_t(METHOD_LABEL[mid]))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("bl-seg")
            b.connect("clicked", lambda _b, m=mid: _pick(m))
            segs[mid] = b
            seg_box.pack_start(b, True, True, 0)
        card.pack_start(self._field(_t("How it is paid"), seg_box),
                        False, False, 0)

        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.pack_start(stack, False, False, 0)

        address = Gtk.TextView()
        address.get_style_context().add_class("bl-addredit")
        address.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        address.set_left_margin(8)
        address.set_right_margin(8)
        address.set_top_margin(6)
        address.set_bottom_margin(6)
        address.get_buffer().set_text(bill["address"] if editing else "")
        addr_scroll = Gtk.ScrolledWindow()
        # AUTOMATIC, not NEVER: a TextView's minimum width is its longest
        # unbreakable run, and NEVER re-propagates that up, so one long address
        # line would push the whole sheet wider than the screen.
        addr_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.AUTOMATIC)
        addr_scroll.set_size_request(-1, 84)
        addr_scroll.add(address)
        addr_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        addr_frame.get_style_context().add_class("bl-addrframe")
        addr_frame.pack_start(addr_scroll, True, True, 0)
        addr_row = self._field(_t("Address"), addr_frame)

        lead = Gtk.SpinButton.new_with_range(0, MAX_LEAD, 1)
        lead.set_value(bill["lead"] if editing else 5)
        lead.set_size_request(72, -1)
        lead_hold = Gtk.Box()
        lead_hold.pack_start(lead, False, False, 0)
        lead_row = self._field(_t("Days in the post"), lead_hold)

        phone = Gtk.Entry()
        phone.set_text(bill["phone"] if editing else "")
        phone_row = self._field(_t("Phone number"), phone)

        note = Gtk.Entry()
        note.set_text(bill["note"] if editing else "")
        note.set_placeholder_text(_t("Anything needed to make the payment"))
        note_row = self._field(_t("Notes"), note)

        for w in (addr_row, lead_row, phone_row, note_row):
            stack.pack_start(w, False, False, 0)

        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("bl-ferr")
        err.set_line_wrap(True)
        err.set_max_width_chars(48)
        card.pack_start(err, False, False, 0)

        def _pick(mid):
            method["id"] = mid
            for other, btn in segs.items():
                ctx = btn.get_style_context()
                if other == mid:
                    ctx.add_class("on")
                else:
                    ctx.remove_class("on")
            # show_all()/hide(), NOT set_visible(): a row's LABEL and ENTRY are
            # separate widgets, and set_visible on their container reveals an
            # empty box — the sheet had a gap where the address should be and
            # nothing in it to type into. (set_no_show_all is not the answer
            # either: it makes show_all() on the widget itself a no-op, which
            # was the same empty gap by a different route.)
            for w, on in ((addr_row, mid == "mail"),
                          (lead_row, mid == "mail"),
                          (phone_row, mid in ("mail", "phone")),
                          (note_row, mid != "mail")):
                if on:
                    w.show_all()
                else:
                    w.hide()
            self._centre_overlay()

        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(6)
        actions.pack_start(Gtk.Box(), True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("bl-quiet")
        cancel.connect("clicked", lambda *_: self._close_overlay())
        actions.pack_start(cancel, False, False, 0)
        save = Gtk.Button(label=_t("Save Bill") if editing else _t("Add Bill"))
        save.set_relief(Gtk.ReliefStyle.NONE)
        save.get_style_context().add_class("bl-primary")
        actions.pack_start(save, False, False, 0)
        card.pack_start(actions, False, False, 0)

        def _commit(*_a):
            # EVERY value is read while the widgets are still alive. Closing
            # the overlay first destroys them, and a Gtk.Entry that has been
            # destroyed returns "" from get_text() — which is how a form once
            # saved a bill with no payee on it however much had been typed.
            name = payee.get_text().strip()
            if not name:
                err.set_text(_t("A bill needs a payee."))
                payee.grab_focus()
                return
            raw = amount.get_text().strip()
            cents = parse_money(raw) if raw else None
            if raw and cents is None:
                err.set_text(_t("The amount is not a number."))
                amount.grab_focus()
                return
            buf = address.get_buffer()
            addr = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            every = REPEATS[repeat.get_active()] \
                if 0 <= repeat.get_active() < len(REPEATS) else 1
            record = {
                "payee": name,
                "account": account.get_text().strip(),
                "amount": cents,
                "due": self._date_of(date_parts),
                "every": every,
                "method": method["id"],
                "address": addr.strip(),
                "phone": phone.get_text().strip(),
                "note": note.get_text().strip(),
                "lead": int(lead.get_value()),
            }
            self._close_overlay()
            if editing:
                # The payment history is the app's record of what was actually
                # done and is never touched by an edit to the bill's details.
                target = self._bill(bill["id"])
                if target is None:
                    return
                record["id"] = target["id"]
                record["paid"] = target["paid"]
                self.bills[self.bills.index(target)] = record
                self.sel = record["id"]
                self._save()
                self._refresh()
                self._flash(_t("Bill saved"))
            else:
                record["id"] = "b%d%s" % (len(self.bills),
                                          os.urandom(3).hex())
                record["paid"] = []
                self.bills.append(record)
                self.sel = record["id"]
                self._save()
                self._refresh()
                self._flash(_t("Bill added"))

        save.connect("clicked", _commit)
        payee.connect("activate", _commit)
        account.connect("activate", _commit)
        amount.connect("activate", _commit)

        self._show_overlay(card, lambda: _pick(method["id"]))
        payee.grab_focus()

    # -- record a payment ----------------------------------------------------

    def _open_payment(self, bid=None):
        bill = self._bill(bid)
        if bill is None:
            return
        self._close_menu()
        info = due_info(bill)
        card = self._card(_t("Record Payment"), bill["payee"])
        card.set_size_request(470, -1)

        amount = Gtk.Entry()
        amount.set_text(money(bill["amount"]).replace("$", "")
                        if bill["amount"] is not None else "")
        card.pack_start(self._field(_t("Amount paid"), amount, 165),
                        False, False, 0)

        row, date_parts = self._date_row(_t("Paid on"), today_key())
        card.pack_start(row, False, False, 0)

        ref = Gtk.Entry()
        card.pack_start(self._field(_t(REF_LABEL[bill["method"]]), ref, 165),
                        False, False, 0)

        # Which occurrence this payment settles. Stated rather than implied:
        # recording a payment is what moves the bill on to its next due date,
        # and a bill that jumped forward for no visible reason is a bill nobody
        # trusts.
        settles = info["due"]
        if settles:
            note = Gtk.Label(label=_t("Settles the payment due %s")
                             % fmt_due(settles), xalign=0)
        else:
            note = Gtk.Label(label=_t("This bill has nothing outstanding."),
                             xalign=0)
        note.get_style_context().add_class("bl-cardmsg")
        note.set_line_wrap(True)
        note.set_max_width_chars(44)
        card.pack_start(note, False, False, 0)

        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("bl-ferr")
        card.pack_start(err, False, False, 0)

        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(4)
        actions.pack_start(Gtk.Box(), True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("bl-quiet")
        cancel.connect("clicked", lambda *_: self._close_overlay())
        actions.pack_start(cancel, False, False, 0)
        done = Gtk.Button(label=_t("Record Payment"))
        done.set_relief(Gtk.ReliefStyle.NONE)
        done.get_style_context().add_class("bl-primary")
        actions.pack_start(done, False, False, 0)
        card.pack_start(actions, False, False, 0)

        def _commit(*_a):
            raw = amount.get_text().strip()
            cents = parse_money(raw) if raw else None
            if raw and cents is None:
                err.set_text(_t("The amount is not a number."))
                amount.grab_focus()
                return
            payment = {"on": self._date_of(date_parts),
                       "for": settles,
                       "amount": cents,
                       "method": bill["method"],
                       "ref": ref.get_text().strip()[:80]}
            self._close_overlay()
            target = self._bill(bill["id"])
            if target is None:
                return
            target["paid"].append(payment)
            self._save()
            self._refresh()
            self._flash(_t("Payment recorded"))

        done.connect("clicked", _commit)
        amount.connect("activate", _commit)
        ref.connect("activate", _commit)
        self._show_overlay(card)
        (ref if bill["amount"] is not None else amount).grab_focus()

    # -- delete --------------------------------------------------------------

    def _confirm_delete(self, bid=None):
        bill = self._bill(bid)
        if bill is None:
            return
        self._close_menu()
        name = bill["payee"]
        if len(name) > 40:
            name = name[:40] + "…"
        card = self._card(
            _t("Delete Bill"),
            _t("“%s” and its payment history will be removed. "
               "This cannot be undone.") % name)
        card.set_size_request(390, -1)

        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(4)
        actions.pack_start(Gtk.Box(), True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("bl-quiet")
        cancel.connect("clicked", lambda *_: self._close_overlay())
        actions.pack_start(cancel, False, False, 0)
        gone = Gtk.Button(label=_t("Delete"))
        gone.set_relief(Gtk.ReliefStyle.NONE)
        gone.get_style_context().add_class("bl-danger")
        gone.connect("clicked", lambda *_: self._do_delete(bill["id"]))
        actions.pack_start(gone, False, False, 0)
        card.pack_start(actions, False, False, 0)
        self._show_overlay(card)
        # Cancel takes the focus, so a stray Return on a confirm cannot be the
        # keystroke that deletes a record.
        cancel.grab_focus()

    def _do_delete(self, bid):
        self._close_overlay()
        bill = self._bill(bid)
        if bill is None:
            return
        i = self.bills.index(bill)
        self.bills.pop(i)
        self.sel = self.bills[min(i, len(self.bills) - 1)]["id"] \
            if self.bills else ""
        self._save()
        self._refresh()
        self._flash(_t("Bill deleted"))

    # -- export / print ------------------------------------------------------

    def _render_pdf(self, path):
        """One page per printed report: every bill, what it costs, when it is
        due and how it is paid. This is the copy that goes by the phone."""
        surf, _cr, text = nbprint.report_page(path)
        text.emit(_t("Bills"), 20, bold=True)
        text.emit(fmt_date(today_key(), year=True), 10, color="#6E695E")
        text.rule()
        pairs = self._ordered()
        if not pairs:
            text.emit(_t("No bills"), 11, color="#6E695E")
        for bill, info in pairs:
            amt = money(bill["amount"]) if bill["amount"] is not None \
                else _t("Amount varies")
            text.emit("%s   %s" % (bill["payee"], amt), 13, bold=True)
            line = [info["state"]]
            if bill["account"]:
                line.append(_t("Account %s") % bill["account"])
            line.append(_t(METHOD_LABEL[bill["method"]]))
            text.emit("  ·  ".join(line), 10, color="#6E695E")
            if bill["method"] == "mail" and bill["address"]:
                for ln in bill["address"].splitlines():
                    if ln.strip():
                        text.emit(ln.strip(), 11)
                if info["post_by"]:
                    text.emit(_t("Post by %s") % fmt_date(info["post_by"],
                                                          year=True), 11)
            elif bill["method"] == "phone" and bill["phone"]:
                text.emit(bill["phone"], 11)
                if bill["note"]:
                    text.emit(bill["note"], 10, color="#6E695E")
            elif bill["note"]:
                text.emit(bill["note"], 10, color="#6E695E")
            text.rule(gap_after=10.0)
        surf.finish()

    def _export_pdf(self):
        self._close_menu()
        # The name is fixed, so every export lands on the last one — and on
        # anything else in Documents that happens to be called Bills.pdf. It
        # used to destroy either without a word. Journal, Novel, Cookbook and
        # Academics were all given this question; bills was not on that list
        # and kept the defect. Same three strings, already in all seventeen
        # catalogs, so there is one wording for "you are about to overwrite".
        if os.path.exists(os.path.join(DOCS_DIR, PDF_NAME)):
            self._confirm_replace(PDF_NAME, self._write_export_pdf)
            return
        self._write_export_pdf()

    def _confirm_replace(self, name, then):
        """Ask before writing over a file that is already in Documents."""
        card = self._card(_t("Replace file?"),
                          _t("“%s” already exists in Documents. Replace it?")
                          % name)
        card.set_size_request(390, -1)
        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(4)
        actions.pack_start(Gtk.Box(), True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("bl-quiet")
        cancel.connect("clicked", lambda *_: self._close_overlay())
        actions.pack_start(cancel, False, False, 0)
        go = Gtk.Button(label=_t("Replace"))
        go.set_relief(Gtk.ReliefStyle.NONE)
        go.get_style_context().add_class("bl-danger")
        go.connect("clicked", lambda *_: (self._close_overlay(), then()))
        actions.pack_start(go, False, False, 0)
        card.pack_start(actions, False, False, 0)
        self._show_overlay(card)
        # Cancel focused, as on the delete confirm: a stray Return must not be
        # the keystroke that overwrites a file.
        cancel.grab_focus()

    def _write_export_pdf(self):
        """Render to Documents/Bills.pdf. Split out so the replace question can
        be answered before anything is written."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            self._render_pdf(os.path.join(DOCS_DIR, PDF_NAME))
        except Exception as exc:                                # noqa: BLE001
            self._flash(_t("The report was not written. %s")
                        % (getattr(exc, "strerror", "") or _t("Unknown cause")))
            return
        self._flash(_t("Saved to Documents as %s") % PDF_NAME)

    def _print(self):
        self._close_menu()
        nbprint.print_document(self, self._render_pdf, job_name="Bills")

    # -- menus ---------------------------------------------------------------

    def _set_sort(self, how):
        self.sort = how
        self._refresh()

    def menu_items(self, name):
        has = self._bill() is not None
        if name == "File":
            return [
                ("Add Bill…", lambda: self._open_form(None)),
                ("Delete Bill…", self._confirm_delete if has else None),
                nbapp.SEP,
                ("Export to PDF", self._export_pdf),
                ("Print…", self._print),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Bill":
            return [
                ("Record Payment…",
                 (lambda: self._open_payment()) if has else None),
                ("Edit Bill…",
                 (lambda: self._open_form(self._bill())) if has else None),
                nbapp.SEP,
                ("Delete Bill…", self._confirm_delete if has else None),
            ]
        if name == "View":
            return [(self.SORT_LABEL[how],
                     None if self.sort == how
                     else (lambda h=how: self._set_sort(h)))
                    for how in self.SORTS]
        return super().menu_items(name)

    # -- keys ----------------------------------------------------------------

    def _on_key(self, w, ev):
        # Esc takes down the open overlay first — a half-filled bill sheet is
        # exactly what a person reaches for Esc to back out of, and without
        # this it closed the whole app from under them. It never deletes
        # anything: Esc leaves, it does not act (see the OS-wide rule).
        if ev.keyval == Gdk.KEY_Escape and self._close_overlay():
            return True
        return super()._on_key(w, ev)


if __name__ == "__main__":
    nbapp.run(Bills)
