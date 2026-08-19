#!/usr/bin/env python3
"""Real-use regression drive for Bill Tracker, on the real widget tree.

    NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \\
        tools/bills_realuse_selftest.py

Each check below is something a person did with the app — File > Add Bill and
the sheet's own controls, the action band's buttons, the Edit sheet — driven
through tools/appdrive on an offscreen holder at 1024x740, the smallest panel
this OS supports. Every check is named; a check fails by name, never by crash.

  day of the month is a rule   a bill given the 31st in a SHORT month kept the
                               30th as its anchor and became a 30th bill for
                               ever: October, December and January all came due
                               on the 30th, and the reopened sheet showed 30.
                               The picked day is now the bill's rule (`dom`),
                               and the sheet says which day the next one lands
                               on instead of rewriting it in silence.
  an amount is not rewritten   17 digits were capped to 10**11 cents, saved,
                               and confirmed with "Bill added" — a bill nobody
                               typed, reading $1,000,000,000.00. It is refused
                               now, and the sheet says which refusal it is.
  a disabled slab looks it     on a settled bill "Record Payment" is
                               insensitive, and was drawn as the live ink slab:
                               a black button that did nothing when clicked.
  days in the post is stated   a settled posted bill said "DAYS IN THE POST
                               None" while its own Edit sheet said 5.
  a sheet that fits does not   an Edit sheet 2px taller than the card measured
  scroll                       before its CJK entries had settled grew a
                               scrollbar and lost 20px of its width to it.

RED PROOFS (M1), measured, each mutation applied ALONE to the app file:

  1. `record["dom"]` dropped from the bill sheet's _commit
       FAIL a bill given the 31st in a short month stays a 31st bill
       FAIL editing a monthly bill into February keeps its 31st
       FAIL the reopened sheet shows the day that was picked
  2. `_day_note` never called (the connects removed)
       FAIL the sheet says which day a short month lands the bill on
  3. `parse_money` capping again (`cents = min(cents, MAX_CENTS)`)
       FAIL an amount over the cap is refused, not saved as another figure
       FAIL ...and the sheet says the amount is too large
  4. `.bl-primary:disabled` removed from the stylesheet
       FAIL a settled bill's Record Payment slab is drawn as disabled
  5. `_facts` printing the lead inside `if info["post_by"]` again
       FAIL a settled posted bill states its own days in the post
  6. `_centre_overlay`'s settle pass removed
       FAIL an edit sheet that fits the panel shows no scrollbar
       FAIL ...and is as wide as the add sheet
"""
import os
import sys
import json
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="bills-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]
HOME = os.path.join(HOME_ROOT, "bills")
STORE = os.path.join(HOME, ".config", "notebook", "bills.json")

import appdrive                                                   # noqa: E402
import cairo                                                      # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name
          + (("\n     <- %s" % (detail,)) if (detail and not cond) else ""))


def fresh(records=None):
    shutil.rmtree(HOME, ignore_errors=True)
    if records is not None:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        with open(STORE, "w", encoding="utf-8") as fh:
            json.dump({"bills": records}, fh, ensure_ascii=False)
    return appdrive.Drive("bills")


def sheet(d):
    """The open card's fields, by the label beside them."""
    out = {}

    def walk(w):
        if isinstance(w, Gtk.Box):
            ch = w.get_children()
            if len(ch) == 2 and isinstance(ch[0], Gtk.Label) and \
                    "bl-flabel" in ch[0].get_style_context().list_classes():
                out[ch[0].get_text()] = ch[1]
                return
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    if d.app._overlay_card is not None:
        walk(d.app._overlay_card)
    return out


def message(d):
    """(text, classes) of the sheet's one message line."""
    if d.app._overlay_card is None:
        return "", []
    for w in d.walk(d.app._overlay_card):
        if isinstance(w, Gtk.Label):
            cls = w.get_style_context().list_classes()
            if "bl-ferr" in cls or "bl-cardmsg" in cls:
                return w.get_text(), cls
    return "", []


def type_into(d, entry, text):
    entry.grab_focus()
    entry.set_text("")
    d.type(text)


def add_sheet(d, payee="", amount=None, day=None, month=None, year=None,
              repeat=None, method=None, lead=None):
    d.menu_action("File", "Add Bill")
    d.pump(0.1)
    f = sheet(d)
    if payee:
        type_into(d, f["Payee"], payee)
    if amount is not None:
        type_into(d, f["Amount"], amount)
    dspin, mcombo, yspin = f["Next due"].get_children()
    if year is not None:
        yspin.set_value(year)
    if month is not None:
        mcombo.set_active(month - 1)
    if day is not None:
        dspin.set_value(day)
    if repeat is not None:
        f["Repeats"].set_active(repeat)
    if method is not None:
        for b in f["How it is paid"].get_children():
            if b.get_label() == method:
                b.clicked()
    if lead is not None:
        f["Days in the post"].get_children()[0].set_value(lead)
    d.pump(0.15)
    return f


def bill_of(d, payee):
    for b in d.app.bills:
        if b["payee"] == payee:
            return b
    return None


def pixel(path, x, y):
    surf = cairo.ImageSurface.create_from_png(path)
    data, stride = surf.get_data(), surf.get_stride()
    o = y * stride + x * 4
    return (data[o + 2], data[o + 1], data[o])


def slab_pixel(d, button, shot_path):
    """The colour of the button's own slab, clear of its label."""
    a = button.get_allocation()
    wx, wy = button.translate_coordinates(d.child, 0, 0)
    return pixel(shot_path, wx + 8, wy + a.height // 2)


# ------------------------------------------------- the day of the month rule
def t_day_of_month_rule():
    d = fresh([])
    try:
        add_sheet(d, payee="Rent", amount="1850", day=31, month=9, year=2026)
        said, classes = message(d)
        check("the sheet says which day a short month lands the bill on",
              "30 September 2026" in said and "bl-ferr" not in classes,
              "%r %s" % (said, classes))
        d.click("Add Bill")
        d.pump(0.2)
        bill = bill_of(d, "Rent")
        due = list(d.mod.occurrences(bill))[:5] if bill else []
        check("a bill given the 31st in a short month stays a 31st bill",
              due == ["2026-09-30", "2026-10-31", "2026-11-30", "2026-12-31",
                      "2027-01-31"], due)
        check("...and its first one is the day that month really has",
              bill and bill["due"] == "2026-09-30", bill and bill["due"])

        # The same bill edited into February — the sheet's own second route to
        # the anchor, and the one that used to pin a 31st bill to the 28th.
        d.app.sel = bill["id"]
        d.menu_action("Bill", "Edit Bill")
        d.pump(0.1)
        f = sheet(d)
        dspin, mcombo, yspin = f["Next due"].get_children()
        check("the reopened sheet shows the day that was picked",
              dspin.get_value() == 31, dspin.get_value())
        yspin.set_value(2027)
        mcombo.set_active(1)
        d.pump(0.15)
        d.click("Save Bill")
        d.pump(0.2)
        bill = bill_of(d, "Rent")
        due = list(d.mod.occurrences(bill))[:4] if bill else []
        check("editing a monthly bill into February keeps its 31st",
              due == ["2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"],
              due)

        # A day the month HAS is not commented on.
        add_sheet(d, payee="Water", amount="40", day=12, month=9, year=2026)
        said, _cls = message(d)
        check("a day the month has needs no note", said == "", said)
        d.click("Add Bill")
        d.pump(0.2)
        bill = bill_of(d, "Water")
        check("...and that bill repeats on the day it was given",
              list(d.mod.occurrences(bill))[:3]
              == ["2026-09-12", "2026-10-12", "2026-11-12"],
              list(d.mod.occurrences(bill))[:3])
    finally:
        d.close()


# ------------------------------------------------------- an amount is kept
def t_amount_is_never_rewritten():
    d = fresh([])
    try:
        add_sheet(d, payee="Huge", amount="99999999999999999")
        d.click("Add Bill")
        d.pump(0.2)
        check("an amount over the cap is refused, not saved as another figure",
              d.app.bills == [], [(b["payee"], b["amount"])
                                  for b in d.app.bills])
        said, classes = message(d)
        check("...and the sheet says the amount is too large",
              said == "The amount is too large." and "bl-ferr" in classes,
              "%r %s" % (said, classes))
        check("...with the sheet still open to correct it",
              d.app._overlay_layer is not None)
        # The other refusal keeps its own words. Each on a sheet of its own,
        # so a sheet that wrongly closed on the check above cannot turn the
        # ones below it into a crash instead of a named failure.
        if d.app._overlay_layer is not None:
            d.key("Escape")
            d.pump(0.1)
        add_sheet(d, payee="Prose", amount="about ninety")
        d.click("Add Bill")
        d.pump(0.2)
        said, _cls = message(d)
        check("a figure with no number in it is still not a number",
              said == "The amount is not a number.", said)
        if d.app._overlay_layer is not None:
            d.key("Escape")
            d.pump(0.1)
        add_sheet(d, payee="Plain", amount="84.20")
        d.click("Add Bill")
        d.pump(0.2)
        check("...and a figure the app keeps is saved as typed",
              [b["amount"] for b in d.app.bills if b["payee"] == "Plain"]
              == [8420], [(b["payee"], b["amount"]) for b in d.app.bills])
    finally:
        d.close()


# ------------------------------------- a settled bill: the band and the facts
def t_settled_bill():
    d = fresh([])
    try:
        add_sheet(d, payee="Alvarez", amount="80", day=25, month=8, year=2026,
                  repeat=5, method="By post", lead=5)
        d.click("Add Bill")
        d.pump(0.2)
        live = d.shot(os.path.join(HOME_ROOT, "band_live.png"))
        pay = d.button("Record Payment")
        live_px = slab_pixel(d, pay, live)
        d.click("Record Payment")
        d.pump(0.1)
        [b for b in d.walk(d.app._overlay_card)
         if isinstance(b, Gtk.Button) and b.get_label() == "Record Payment"
         ][0].clicked()
        d.pump(0.25)
        bill = bill_of(d, "Alvarez")
        info = d.mod.due_info(bill)
        check("recording the payment settles the one-off bill",
              info["kind"] == "settled", info["kind"])
        settled = d.shot(os.path.join(HOME_ROOT, "band_settled.png"))
        pay = d.button("Record Payment")
        check("...and its Record Payment button is not sensitive",
              not pay.get_sensitive())
        settled_px = slab_pixel(d, pay, settled)
        check("a settled bill's Record Payment slab is drawn as disabled",
              settled_px != live_px,
              "live %s settled %s" % (live_px, settled_px))
        check("...in the OS's grey for a disabled primary",
              settled_px == (0xC9, 0xC4, 0xB6), settled_px)

        facts = dict((label, value)
                     for label, value, _tone, _own in d.app._facts(bill, info))
        check("a settled posted bill states its own days in the post",
              facts.get("DAYS IN THE POST") == "5", facts)
        check("...the same number its Edit sheet holds",
              str(bill["lead"]) == "5", bill["lead"])
        # A posted bill with no lead days still says it has none.
        bill["lead"] = 0
        facts = dict((label, value)
                     for label, value, _tone, _own in d.app._facts(bill, info))
        check("a posted bill with no days allowed says None",
              facts.get("DAYS IN THE POST") == "None", facts)
    finally:
        d.close()


# ------------------------------------------- a sheet that fits does not scroll
def t_sheet_that_fits():
    # A payee and an account in Japanese: the entries measure two pixels taller
    # once their fallback face is resolved, which is AFTER the card has been
    # measured for the scroller around it.
    d = fresh([{"id": "b0", "payee": "Pacific Gas & Electric, Inc.",
                "account": "PG-0091 447 / 日本", "amount": 8420,
                "due": "2027-02-28", "every": 1, "method": "mail",
                "address": "PO Box 997300\nSacramento, CA 95899-7300",
                "phone": "1-800-743-5000", "note": "", "lead": 5, "paid": []}])
    try:
        d.menu_action("Bill", "Edit Bill")
        d.pump(0.3)
        card, fit = d.app._overlay_card, d.app._overlay_fit
        want = card.get_preferred_size()[1]
        edit_w = card.get_allocation().width
        check("an edit sheet that fits the panel shows no scrollbar",
              not fit.get_vscrollbar().get_child_visible()
              and want.height <= 740 - 48,
              "card %dx%d, fit %s, bar %s"
              % (want.width, want.height, fit.get_size_request(),
                 fit.get_vscrollbar().get_child_visible()))
        d.key("Escape")
        d.pump(0.1)
        d.menu_action("File", "Add Bill")
        d.pump(0.3)
        add_w = d.app._overlay_card.get_allocation().width
        check("...and is as wide as the add sheet",
              edit_w == add_w, "edit %d, add %d" % (edit_w, add_w))
    finally:
        d.close()


for fn in (t_day_of_month_rule, t_amount_is_never_rewritten, t_settled_bill,
           t_sheet_that_fits):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))

bad = [n for n, ok in RESULTS if not ok]
print("\nRESULT: %s (%d checks, %d failed)"
      % ("PASS" if not bad else "FAILED", len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
