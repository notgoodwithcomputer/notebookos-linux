#!/usr/bin/env python3
"""A choice the ledger accepts, it keeps — and saving keeps what it was given.

Two defects, both of the same family: the app acted on something and then wrote
a file that did not contain it.

  * VIEW ▸ HIDE BALANCE CHART lasted until the window closed. The toggle only
    ever touched the live widget, so a chart the user turned off was back at the
    next launch — a preference accepted, acted on, and quietly forgotten. This
    is the shape recorded in the campaign's `settings-must-leave-the-process`
    note: applied to the running process, never written down.
  * `_autosave` REBUILT the file from `{"tx", "opening"}`, so any other
    top-level key was deleted by the act of saving. A store written by a newer
    build, or hand-edited, lost whatever this version did not happen to know
    about. Academics had the identical bug.

WHY get_visible() IS NOT THE THING TO SAVE. A window that has not been realised
reports every child invisible, so reading the widget at save time would persist
"hidden" for any session that closed early. The preference is tracked as a flag
and the widget follows it, not the other way round. The first version of the
probe that found this bug was itself wrong for exactly this reason: it asked an
unrealised widget and got False, then called that "persisted".

RED PROOFS (M1), measured, each mutation alone:

  1. the toggle stops recording the choice
     (`self._chart_shown = not self.chartwrap.get_visible()` -> no assignment)
       FAIL hiding the chart survives a restart
            <- the chart came back
  2. the saved preference is not applied at startup
     (`self.chartwrap.set_visible(...)` -> `set_visible(True)`)
       FAIL hiding the chart survives a restart   <- the chart came back
  3. `_autosave` stops carrying unknown keys (`payload = dict(self._extra ...)`
     -> `payload = {}`)
       FAIL a key this version does not know survives a save
            <- 'ledger_name' gone
  4. the opening row is revealed whatever the balance
     (`nbtransitions.reveal(rev, bool(_cents(self.opening)))`
      -> `nbtransitions.reveal(rev, True)`)
       FAIL an opening balance of 0.0 is hidden in the sidebar
            <- reveal_child=True, label='$0.00'
       FAIL an opening balance of 0.004 is hidden in the sidebar
            <- reveal_child=True, label='$0.00'

     The row moved into a Gtk.Revealer when the OS motion rule changed to
     "animate EVERY state change" (PAPER-PHYSICS Amendment 3): it used to
     SNAP into the middle of the figures via set_visible(). Measured after the
     change: slide-down, 160ms. This suite reads `get_reveal_child()` now —
     the ROW's own `get_visible()` is True even while the revealer holds it
     closed, so the old check would have gone vacuous rather than red.
  5. the opening figure is never filled in
     (`self.opening_lbl.set_text(self._money(self.opening))` -> `_money(0)`)
       FAIL ...and it reads as the opening balance  <- ('$0.00', '$2,400.00')
       FAIL the sidebar figures add up to the balance at opening 2400.0
            <- balance='$3,850.00', opening+credit-debit=1450.0, expected 3850.0
       (and the same pair for the overdrawn -500.0 case)

THE SIDEBAR HAS TO ADD UP — the last section. CREDIT and DEBIT are sums of the
entries; BALANCE includes the opening. With a non-zero opening those three
disagreed by exactly the term that was not on screen: CREDIT +$1,105.00 and
DEBIT -$2,280.74 against a BALANCE of $1,224.26. The ledger has always stored
`opening` and the report card has always printed it; the sidebar — the figures
somebody actually looks at — left it out. The reconciliation is computed from
the LABELS the user reads, not from the model behind them, or it would only be
checking that arithmetic still works.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctprefs-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/accounting.json"

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


def pump(n=500):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def realise(app):
    """Show the tree the way the desktop does. Without this every child reports
    itself invisible and every visibility check is meaningless."""
    off = Gtk.OffscreenWindow()
    child = app.get_child()
    app.remove(child)
    off.add(child)
    off.set_size_request(1024, 722)
    off.show_all()
    pump()
    off.get_pixbuf()
    pump()
    return off


def seed(**extra):
    doc = {"opening": 100.0,
           "tx": [{"date": "01 Aug", "iso": "2026-08-01", "desc": "Rent",
                   "amt": -950.0}]}
    doc.update(extra)
    with open(STORE, "w") as f:
        json.dump(doc, f)


# ------------------------------------------------------- the chart preference
seed()
app = accounting.Accounting()
pump()
off = realise(app)
# ASK ABOUT THE CHART, NOT THE BOX AROUND IT. Checking only
# `chartwrap.get_visible()` is checking a proxy: an attempt at this feature used
# `set_no_show_all(True)` on the wrapper, which stops show_all() recursing into
# its CHILDREN — so the wrapper reported itself visible while the DrawingArea
# inside had a 1x1 allocation and the whole "Balance over time" block was gone
# from the window. Every check here stayed green. The chart's own visibility and
# its allocation are what say it is really on screen.
def chart_really_shown(a):
    alloc = a.chart.get_allocation()
    return (a.chartwrap.get_visible() and a.chart.get_visible()
            and alloc.width > 20 and alloc.height > 20)


check("the chart is shown on a ledger that never said otherwise",
      chart_really_shown(app),
      (app.chartwrap.get_visible(), app.chart.get_visible(),
       app.chart.get_allocation().width, app.chart.get_allocation().height))

app._toggle_chart()
pump()
check("hiding it hides it", not app.chartwrap.get_visible())
check("the View menu now offers to show it",
      "Show" in str(app.menu_items("View")[0][0]),
      app.menu_items("View")[0][0])
app._on_destroy()
off.destroy()
app.destroy()
pump()

with open(STORE) as f:
    saved = json.load(f)
check("the choice reached the file", saved.get("chart") is False,
      saved.get("chart"))

app = accounting.Accounting()
pump()
off = realise(app)
check("hiding the chart survives a restart", not app.chartwrap.get_visible(),
      "the chart came back")
app._toggle_chart()
pump()
check("showing it again also sticks", chart_really_shown(app),
      (app.chartwrap.get_visible(), app.chart.get_visible(),
       app.chart.get_allocation().width))
app._on_destroy()
off.destroy()
app.destroy()
pump()
with open(STORE) as f:
    check("...and that reached the file too",
          json.load(f).get("chart") is True)

# ----------------------------------------------- a ledger with no preference
# An existing store predates this key entirely. It must open with the chart
# shown — the behaviour every user already has — not hidden.
seed()
app = accounting.Accounting()
pump()
off = realise(app)
check("a store written before the preference existed shows the chart",
      chart_really_shown(app),
      (app.chartwrap.get_visible(), app.chart.get_visible(),
       app.chart.get_allocation().width))
off.destroy()
app.destroy()
pump()

# -------------------------------------------------- saving keeps what it read
seed(ledger_name="Household", schema=7)
app = accounting.Accounting()
pump()
app.add_entry("Bookshop", -22.99)      # any edit triggers a save
pump()
app._on_destroy()
app.destroy()
pump()
with open(STORE) as f:
    after = json.load(f)
check("a key this version does not know survives a save",
      after.get("ledger_name") == "Household", after.get("ledger_name"))
check("...and so does a second one", after.get("schema") == 7,
      after.get("schema"))
check("the entries are still there", len(after.get("tx", [])) == 2,
      len(after.get("tx", [])))
check("no unexplained keys were invented",
      set(after) == {"opening", "tx", "chart", "ledger_name", "schema"},
      sorted(after))

# --------------------------------------------------- the opening balance is settable
# `opening` has been in the saved schema from the start: the loader reads it, the
# balance adds it, the Ledger Summary prints it, the running-balance column
# starts from it, and the damage salvage goes to trouble to recover it — and
# there was NO UI TO SET IT. It could only ever be non-zero in a hand-edited or
# imported file. Same shape as a class's `room` in academics: a field the model
# can express and the interface cannot reach, which reads as the app simply not
# having the feature. It matters most on day one, when a ledger you start today
# does not start from nothing.
seed()
app = accounting.Accounting()
pump()
app._overlay_size = lambda: (1024, 722)
labels = [i[0] for i in app.menu_items("Edit") if i and i[0]]
check("the Edit menu offers the opening balance",
      any("opening balance" in str(l).lower() for l in labels), labels)

start = app.balance.get_text()
app._opening_card()
pump()
check("the card opened", hasattr(app, "_o_amt"))
app._o_amt.set_text("2400")
app._save_opening()
pump()
check("the opening balance was set", app.opening == 2400.0, app.opening)
check("...and the headline moved by exactly that much",
      app.balance.get_text() == app._money(2400.0 - 950.0),
      (start, app.balance.get_text()))

# An account can be overdrawn — _parse_amount strips the sign, so direction has
# to come from the card the way it does for an entry.
app._opening_card()
pump()
app._o_amt.set_text("500")
app._set_odir("debit")
app._save_opening()
pump()
check("an overdrawn opening balance is negative", app.opening == -500.0,
      app.opening)
check("...and the headline follows it",
      app.balance.get_text() == app._money(-500.0 - 950.0),
      app.balance.get_text())

# It is an edit like any other: reversible, and NAMED. The name is the only
# thing `undo.checkpoint()` buys in this app — there is no free-text buffer for
# it to flush — so without asserting the label, deleting the checkpoint changes
# nothing any check can see. Measured: it left this suite fully green.
check("the menu names the step it will reverse",
      "Opening Balance" in str([i[0] for i in app.menu_items("Edit")][0]),
      [i[0] for i in app.menu_items("Edit")][0])
app.undo.undo()
pump()
check("undo takes the opening balance back", app.opening == 2400.0, app.opening)

# Rubbish is refused rather than silently taken as zero.
app._opening_card()
pump()
app._o_amt.set_text("not a number")
app._save_opening()
pump()
check("a non-numeric opening balance is refused", app.opening == 2400.0,
      app.opening)
check("...and the card stays open to say so",
      getattr(app, "_edit_layer", None) is not None)
app._close_edit()
pump()

# Empty means zero — the way to clear it.
app._opening_card()
pump()
app._o_amt.set_text("")
app._save_opening()
pump()
check("clearing the field sets it to zero", app.opening == 0.0, app.opening)

app._on_destroy()
app.destroy()
pump()
with open(STORE) as f:
    check("the opening balance persists", json.load(f).get("opening") == 0.0)

# -------------------------------------------- the summary panel has to add up
# CREDIT and DEBIT are sums of the entries; BALANCE includes the opening. With a
# non-zero opening those three disagree by exactly the term that was not on
# screen — a money app showing a summary whose own figures do not reconcile. The
# row is shown only when it is non-zero, because at zero the other three
# reconcile by themselves and the row is noise.
def sidebar_reconciles(a):
    """opening + credit - debit, computed from the LABELS the user reads, not
    from the model behind them."""
    def num(s):
        return float((s or "0").replace(MINUS_CH, "-").replace("$", "")
                     .replace(",", "").replace("+", "") or 0)
    return round(num(a.opening_lbl.get_text()) + num(a.credit_lbl.get_text())
                 - abs(num(a.debit_lbl.get_text())), 2)


MINUS_CH = accounting.MINUS

for opening, tx, shown in (
        (2400.0, [("Rent", -950.0), ("Salary", 2400.0)], True),
        (0.0, [("Rent", -950.0), ("Salary", 2400.0)], False),
        (-500.0, [("Rent", -950.0)], True),
        (0.004, [("Rent", -950.0)], False)):          # sub-cent snaps to zero
    with open(STORE, "w") as f:
        json.dump({"opening": opening,
                   "tx": [{"date": "01 Aug", "iso": "2026-08-01",
                           "desc": d, "amt": v} for d, v in tx]}, f)
    a = accounting.Accounting()
    pump()
    o = realise(a)
    # The row lives in a Gtk.Revealer since the motion rule changed (animate
    # EVERY state change), so REVEAL state is what says whether it is on
    # screen — asking the row's own get_visible() reports True even while the
    # revealer holds it closed, which would make this whole section vacuous.
    rev = a.opening_rev
    check("an opening balance of %s is %s in the sidebar"
          % (opening, "shown" if shown else "hidden"),
          rev.get_reveal_child() is shown,
          "reveal_child=%s, label=%r" % (rev.get_reveal_child(),
                                         a.opening_lbl.get_text()))
    if shown:
        check("...and it reads as the opening balance",
              a.opening_lbl.get_text() == a._money(opening),
              (a.opening_lbl.get_text(), a._money(opening)))
    # The reconciliation must hold whether or not the row is on screen.
    want = round(a.opening + sum(v for _d, v in tx), 2)
    check("the sidebar figures add up to the balance at opening %s" % opening,
          a.balance.get_text() == a._money(want)
          and sidebar_reconciles(a) == want,
          "balance=%r, opening+credit-debit=%r, expected %r"
          % (a.balance.get_text(), sidebar_reconciles(a), want))
    o.destroy()
    a.destroy()
    pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
