#!/usr/bin/env python3
"""Accounting, driven the way a person drives it — one named check per defect
this round of real-use fixed.

    tools/guestrun.sh python3 tools/accounting_realuse_selftest.py

Every check here was RED on the tree before its fix, and each one is named after
the thing a user would have seen:

  ACC-1  Edit ▸ Opening Balance offered "In credit" / "Overdrawn" and marked the
         chosen one with a CSS class no stylesheet defined, so both buttons
         painted identically — measured background (248,247,242) and ink
         (26,25,22) on both, before and after clicking. Read here in PIXELS off
         a synchronous render, because "the class is set" was true the whole
         time it was broken.
  ACC-2  The editor took "31 Feb" and wrote 2026-02-31 into the machine-readable
         column the CSV export sorts on.
  ACC-3  Ten digits of amount pushed the BALANCE column and the Add button off a
         1024px panel, and fourteen digits came back with cents nobody typed
         (99999999999999.99 stored as ...98).
  ACC-4  The status line kept describing an action that had just been undone.
  ACC-5  ".5", "5." and "4,75" were each refused with "Enter an amount" — told
         to type an amount by a field with the amount still in it.
  ACC-6  FIND could not find "Café crème" from "cafe".
  ACC-9  Recovering a damaged ledger dropped the saved "chart hidden" choice.

The UI checks run through tools/appdrive.py: the real widget tree in an
offscreen holder at the smallest supported panel (1024x740), real clicks, real
keys, and shot() for a synchronous render whose pixels can be measured.
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

HOME_ROOT = tempfile.mkdtemp(prefix="nb-acct-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = HOME_ROOT
SHOTS = os.path.join(HOME_ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)

import gi                                                      # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                  # noqa: E402
import cairo                                                   # noqa: E402
import appdrive                                                # noqa: E402
import accounting                                              # noqa: E402

A = accounting.Accounting
FAILED = []
N = [0]


def check(name, ok, detail=""):
    N[0] += 1
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)
    return bool(ok)


def section(fn):
    """Run a block of checks; a crash inside it becomes a named FAIL, never a
    traceback that ends the suite with no result."""
    try:
        fn()
    except Exception as exc:                                   # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("%s ran to the end" % fn.__name__, False, repr(exc))


def store_path():
    return os.path.join(HOME_ROOT, "accounting", ".config", "notebook",
                        "accounting.json")


def fresh():
    shutil.rmtree(os.path.join(HOME_ROOT, "accounting"), ignore_errors=True)
    return appdrive.Drive("accounting")


def add(d, desc, amount, direction="debit"):
    """Type an entry into the real form and commit it with Return."""
    app = d.app
    if not app.form_reveal.get_reveal_child():
        d.menu_action("File", "New Entry")
        d.pump(0.1)
    app.f_desc.grab_focus()
    app.f_desc.set_text("")
    d.type(desc)
    app.f_amt.grab_focus()
    app.f_amt.set_text("")
    d.type(amount)
    (app.btn_credit if direction == "credit" else app.btn_debit).clicked()
    d.key("Return")
    d.pump(0.2)


def rect(d, widget):
    alloc = widget.get_allocation()
    xy = widget.translate_coordinates(d.child, 0, 0)
    return xy[-2], xy[-1], alloc.width, alloc.height


def ink_and_paper(png, box):
    """(most common colour, darkest colour) inside `box` — the paper a control
    is painted on and the ink its label is drawn in."""
    from collections import Counter
    x, y, w, h = box
    surf = cairo.ImageSurface.create_from_png(png)
    data, stride = surf.get_data(), surf.get_stride()
    seen = Counter()
    darkest = (255, 255, 255)
    for yy in range(y + 2, y + h - 2):
        for xx in range(x + 2, x + w - 2):
            off = yy * stride + xx * 4
            px = (data[off + 2], data[off + 1], data[off])
            seen[px] += 1
            if sum(px) < sum(darkest):
                darkest = px
    if not seen:
        return None, None
    return seen.most_common(1)[0][0], darkest


# ---------------------------------------------------------------- ACC-1
def opening_direction_is_visible():
    d = fresh()
    try:
        d.menu_action("Edit", "Opening Balance")
        d.pump(0.2)
        app = d.app
        credit, debit = app._o_btns["credit"], app._o_btns["debit"]
        box_c, box_d = rect(d, credit), rect(d, debit)
        shot = d.shot(os.path.join(SHOTS, "opening_credit.png"))
        chosen, other = ink_and_paper(shot, box_c), ink_and_paper(shot, box_d)
        check("the opening balance card marks the chosen direction in pixels",
              chosen != other and chosen[0] is not None,
              "In credit %s vs Overdrawn %s" % (chosen, other))
        debit.clicked()
        d.pump(0.2)
        shot2 = d.shot(os.path.join(SHOTS, "opening_overdrawn.png"))
        now_c, now_d = ink_and_paper(shot2, box_c), ink_and_paper(shot2, box_d)
        # `now_d != now_c` is not redundant: with the two buttons painting
        # identically (the defect), "the mark moved" would otherwise be true of
        # a card with no mark on it at all.
        check("...and the mark moves to the direction that was clicked",
              app._odir == "debit" and now_d != now_c
              and now_d == chosen and now_c == other,
              "In credit %s, Overdrawn %s" % (now_c, now_d))
        # A pick-one row of toggles restating itself from its own handler is the
        # re-entrancy trap that bit four apps in this OS.
        check("clicking a direction does not re-enter its own handler",
              all(hasattr(b, "get_active") for b in app._o_btns.values())
              and app._o_btns["debit"].get_active()
              and not app._o_btns["credit"].get_active(),
              [type(b).__name__ for b in app._o_btns.values()])
        # ...and the money still follows the direction that is showing.
        app._o_amt.grab_focus()
        d.type("250")
        d.key("Return")
        d.pump(0.2)
        check("the direction the card shows is the one it saves",
              app.opening == -250.0, app.opening)
    finally:
        d.close()


# ---------------------------------------------------------------- ACC-2
def impossible_dates_are_refused():
    d = fresh()
    try:
        add(d, "Rent", "1200")
        was = dict(d.app.tx[0])
        d.app.rows.get_children()[0].clicked()
        d.pump(0.2)
        app = d.app
        app._e_date.grab_focus()
        app._e_date.set_text("")
        d.type("31 Feb")
        d.click("Save")
        d.pump(0.2)
        check("a date the calendar does not have is refused, not saved",
              app.tx[0] == was and getattr(app, "_edit_layer", None) is not None,
              (app.tx[0], app._e_err.get_text()))
        check("...and the editor says so where the person is looking",
              app._e_err.get_visible() and bool(app._e_err.get_text()),
              app._e_err.get_text())
        d.shot(os.path.join(SHOTS, "edit_31feb.png"))
        # Free text in the date column is a DOCUMENTED allowance (see
        # _edited_iso): it stays as the user's own words and simply carries no
        # sortable date. Pinned so the guard above cannot grow into it.
        app._e_date.set_text("")
        d.type("sometime soon")
        d.click("Save")
        d.pump(0.2)
        check("free text in the date is still the user's own words",
              app.tx[0]["date"] == "sometime soon" and "iso" not in app.tx[0],
              app.tx[0])
    finally:
        d.close()


def impossible_dates_never_reach_the_sortable_column():
    old = {"date": "6 Aug", "iso": "2026-08-06"}
    check("an impossible day is never built into the sortable date",
          A._edited_iso(old, "31 Feb") is None
          and A._edited_iso(old, "31 Apr") is None
          and A._edited_iso(old, "29 Feb 2027") is None,
          [A._edited_iso(old, s) for s in ("31 Feb", "31 Apr", "29 Feb 2027")])
    check("...on the add path as well", A._iso_for("31 Feb") == "",
          A._iso_for("31 Feb"))
    check("a date that does exist still moves with the entry",
          A._edited_iso(old, "1 Jan") == "2026-01-01"
          and A._edited_iso(old, "29 Feb 2028") == "2028-02-29",
          (A._edited_iso(old, "1 Jan"), A._edited_iso(old, "29 Feb 2028")))


# ---------------------------------------------------------------- ACC-3
def amounts_stay_inside_the_ledger():
    check("an amount too big to store exactly is refused",
          A._parse_amount("99999999999999.99") is None
          and A._parse_amount("9999999999.99") is None,
          (A._parse_amount("99999999999999.99"),
           A._parse_amount("9999999999.99")))
    check("...and the refusal names the limit instead of asking for an amount",
          A._missing_msg("Rent", None, "9999999999.99")
          == "Enter an amount below $100,000,000.00",
          A._missing_msg("Rent", None, "9999999999.99"))
    d = fresh()
    try:
        add(d, "Typo", "9999999999.99", "credit")
        check("a ten-digit typo is not committed",
              d.app.tx == [], d.app.tx)
        width = d.child.get_preferred_width().minimum_width
        check("...so the ledger still fits the 1024px panel",
              width <= 1024, "%dpx" % width)
        d.shot(os.path.join(SHOTS, "huge_refused.png"))
        # The widest amount the ledger DOES take must fit too, or the ceiling is
        # in the wrong place.
        add(d, "Biggest", "99999999.99", "credit")
        d.pump(0.2)
        width = d.child.get_preferred_width().minimum_width
        check("the largest accepted amount fits the panel as well",
              len(d.app.tx) == 1 and width <= 1024,
              "%d entries, %dpx" % (len(d.app.tx), width))
        check("...and it is stored to the cent that was typed",
              d.app.tx[0]["amt"] == 99999999.99, d.app.tx[0]["amt"])
    finally:
        d.close()


# ---------------------------------------------------------------- ACC-4
def undo_leaves_no_untrue_status():
    d = fresh()
    try:
        add(d, "Coffee", "3.50")
        add(d, "Salary", "2000", "credit")
        app = d.app
        said = app.status_lbl.get_text()
        d.menu_action("Edit", "Undo")
        d.pump(0.2)
        check("undoing an add clears the sentence about the add",
              len(app.tx) == 1 and app.status_lbl.get_text() == "",
              "%d entries, status %r (was %r)"
              % (len(app.tx), app.status_lbl.get_text(), said))
        d.menu_action("Edit", "Redo")
        d.pump(0.2)
        check("redo does not leave the undone sentence either",
              len(app.tx) == 2 and app.status_lbl.get_text() == "",
              app.status_lbl.get_text())
        # the delete route: confirm, then Ctrl+Z
        app.rows.get_children()[0].clicked()
        d.pump(0.2)
        d.click("Delete")
        d.pump(0.2)
        [b for b in d.find(Gtk.Button, label="Delete")][-1].clicked()
        d.pump(0.2)
        deleted = app.status_lbl.get_text()
        d.key("z", ctrl=True)
        d.pump(0.2)
        check("undoing a delete does not leave 'Entry deleted' over the entry",
              len(app.tx) == 2 and app.status_lbl.get_text() == "",
              "%d entries, status %r (was %r)"
              % (len(app.tx), app.status_lbl.get_text(), deleted))
        d.shot(os.path.join(SHOTS, "undo_delete.png"))
        # an action that really did happen still says so
        add(d, "Bread", "2.10")
        check("a committed action still reports itself",
              app.status_lbl.get_text() == "Entry added",
              app.status_lbl.get_text())
    finally:
        d.close()


# ---------------------------------------------------------------- ACC-5
def a_typed_number_is_never_called_missing():
    check("a bare point is an amount (.5, 5.)",
          A._parse_amount(".5") == 0.5 and A._parse_amount("5.") == 5.0,
          (A._parse_amount(".5"), A._parse_amount("5.")))
    for raw in ("4,75", "1,23", "1,23,456"):
        check("%r is not answered with 'Enter an amount'" % raw,
              A._missing_msg("Rent", None, raw) != "Enter an amount",
              A._missing_msg("Rent", None, raw))
    # the shapes this app must keep refusing
    check("mis-grouped and non-ASCII digits are still not money",
          all(A._parse_amount(r) is None
              for r in ("1,23,456", "٣", "１２３", "1.2.3", "-", ".", "")),
          [A._parse_amount(r) for r in ("1,23,456", "٣", "１２３", "1.2.3",
                                        "-", ".", "")])
    d = fresh()
    try:
        add(d, "Half", ".5")
        check("a bare point commits from the form",
              [t["amt"] for t in d.app.tx] == [-0.5], d.app.tx)
        add(d, "Comma amount", "4,75")
        check("a decimal comma is refused with words that fit what was typed",
              len(d.app.tx) == 1 and d.app._f_err.get_visible()
              and d.app._f_err.get_text() != "Enter an amount",
              d.app._f_err.get_text())
        d.shot(os.path.join(SHOTS, "comma_amount.png"))
    finally:
        d.close()


# ---------------------------------------------------------------- ACC-6
def find_folds_accents():
    d = fresh()
    try:
        add(d, "Café crème", "0.10")
        add(d, "Coffee", "3")
        app = d.app
        found = {}
        for term in ("cafe", "café", "creme", "coffee", "zzz"):
            app.search.grab_focus()
            app.search.set_text("")
            d.type(term)
            d.pump(0.4)
            found[term] = len([c for c in app.rows.get_children()
                               if c is not app.empty])
        check("a search typed without accents finds the accented word",
              found["cafe"] == 1 and found["creme"] == 1, found)
        check("...and the accented spelling still finds it",
              found["café"] == 1, found)
        check("...and a search that matches nothing still matches nothing",
              found["zzz"] == 0 and found["coffee"] == 1, found)
        d.shot(os.path.join(SHOTS, "find_cafe.png"))
    finally:
        d.close()


# ---------------------------------------------------------------- ACC-9
def recovery_keeps_the_hidden_chart():
    d = fresh()
    try:
        add(d, "Coffee", "3.50")
        add(d, "Salary", "2000", "credit")
        d.menu_action("View", "Hide Balance Chart")
        d.pump(0.2)
        check("the chart can be hidden", not d.app._chart_shown)
    finally:
        d.close()
    text = open(store_path(), encoding="utf-8").read()
    check("...and the file records it", '"chart": false' in text, text[:60])
    # truncate inside the transaction array — a write cut short by failing
    # media, which is the damage this salvage exists for. The cut lands after
    # the first complete entry so there is real history to get back.
    cut = text.index("},", text.index('"tx"')) + 6
    with open(store_path(), "w", encoding="utf-8") as fh:
        fh.write(text[:cut])
    d = appdrive.Drive("accounting")
    try:
        check("a damaged ledger still recovers its entries",
              len(d.app.tx) >= 1 and "Recovered" in d.app.status_lbl.get_text(),
              (len(d.app.tx), d.app.status_lbl.get_text()))
        check("recovery keeps the chart the user had hidden",
              d.app._chart_shown is False and not d.app.chartwrap.get_visible(),
              (d.app._chart_shown, d.app.chartwrap.get_visible()))
        d.shot(os.path.join(SHOTS, "recovered_chart_hidden.png"))
    finally:
        d.close()
    saved = json.load(open(store_path(), encoding="utf-8"))
    check("...and does not write the default back over it",
          saved.get("chart") is False, saved.get("chart"))


for fn in (opening_direction_is_visible,
           impossible_dates_are_refused,
           impossible_dates_never_reach_the_sortable_column,
           amounts_stay_inside_the_ledger,
           undo_leaves_no_untrue_status,
           a_typed_number_is_never_called_missing,
           find_folds_accents,
           recovery_keeps_the_hidden_chart):
    section(fn)

print("%d checks, %d failed" % (N[0], len(FAILED)))
for name in FAILED:
    print("  FAILED: " + name)
shutil.rmtree(HOME_ROOT, ignore_errors=True)
print("all checks passed" if not FAILED else "RESULT: FAILED")
raise SystemExit(1 if FAILED else 0)
