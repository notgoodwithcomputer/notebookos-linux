#!/usr/bin/env python3
"""Rendered probes for Accounting's entry form and edit/delete path.

RED PROOFS (M1), measured against separate scratch copies:

  1. Removed the strict money-shape guard in `_parse_amount`.
       FAIL amounts that are not money are rejected, and preserved
            <- committed=['1.2.3', '-', '٣', '１２３']
     (The guard first shipped rejecting excess precision and exponents too,
     which contradicted the app's persisted contract — see the comment above
     the accepted list — so "1e5" and "12.345" moved to the accepted side and
     out of this proof's output.)
  2. Restored separate `strftime` calls when an entry is committed.
       FAIL the visible date and persisted ISO date are one snapshot
            <- ('31 Dec', '1 Jan', '2027-01-01')
  3b. the open form never re-checks the day
     (`self._stamp_today()\n            self._start_form_clock()`
      -> `self._stamp_today()`)
       FAIL ...and the open form keeps its clock armed   <- None
  3c. the form clock is never stopped (`else: self._stop_form_clock()`
      -> `else: pass`)
       FAIL closing the form retires the clock   <- 28

     NOTE what 3b does NOT turn red: the two midnight-rollover checks call
     `_tick_form_clock()` directly, so they still pass with nothing scheduled to
     call it. That is the handler-versus-binding split — the tick's BEHAVIOUR and
     the fact that it is ARMED are separate facts, and each has its own check
     here. Neither alone would catch the defect.
  3. Replaced `entry = dict(self.tx[idx]); entry.update(...)` with the old
     three-field reconstruction.
       FAIL editing preserves transaction identity metadata
            <- {'date': '2 Aug', 'desc': 'Rent revised', 'amt': -951.0,
                'iso': '2026-08-02'}

Each mutation was made only in /tmp/accounting-form-red-*/accounting.py and run
with ACCOUNTING_MODULE_DIR pointing there. The application source was never
used as a red-proof target.
"""
import json
import os
import shutil
import sys

H = "/tmp/nbhome-accounting-form-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/accounting.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("ACCOUNTING_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk                            # noqa: E402
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


def seed(tx=None):
    with open(STORE, "w") as f:
        json.dump({"opening": 1000.0, "tx": tx or []}, f)


def buttons(widget):
    out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            out.extend(buttons(child))
    return out


seed()
app = accounting.Accounting()
off = realise(app)
app._toggle_form()
pump()
check("the entry form really opens", app.form_reveal.get_reveal_child())

# Valid conventional forms commit their exact cent value. The last two carry
# more precision than a cent, or an exponent, and are ROUNDED rather than
# refused: that is the app's persisted contract (pinned independently by
# accounting_selftest) and the refusal path cannot explain itself here — its
# only voice is `_missing_msg`, whose docstring records that answering "Enter an
# amount" to somebody who plainly typed one reads as a bug. Shape is still
# validated; only precision is forgiven.
for raw, expected in (("1,234.56", -1234.56), ("+12.34", -12.34),
                      ("12.345", -12.35), ("1e5", -100000.0)):
    before = len(app.tx)
    app.f_desc.set_text("Amount %s" % raw)
    app.f_amt.set_text(raw)
    app._on_add()
    pump()
    check("%s commits as money" % raw,
          len(app.tx) == before + 1 and app.tx[-1]["amt"] == expected,
          app.tx[-1] if len(app.tx) > before else "not committed")

# These are not money at all — a malformed number, a bare sign, blanks, a value
# that overflows to infinity, and digits outside ASCII (float() reads "٣" as 3.0
# on its own, which is how a non-ASCII digit used to become a silent amount).
# A rejection must leave both fields intact so correction does not mean retyping
# the transaction.
bad = ("1.2.3", "-", "٣", "1e309", "", "   ", "１２３")
committed = []
lost = []
for raw in bad:
    app.f_desc.set_text("Keep this description")
    app.f_amt.set_text(raw)
    before = len(app.tx)
    app._on_add()
    pump()
    if len(app.tx) != before:
        committed.append(raw)
    if (app.f_desc.get_text() != "Keep this description" or
            app.f_amt.get_text() != raw):
        lost.append(raw)
check("amounts that are not money are rejected, and preserved",
      not committed and not lost, "committed=%r; lost=%r" % (committed, lost))

# One localtime snapshot supplies label, short stored date, and ISO stored date.
class Clock(object):
    def __init__(self):
        self.value = time_tuple(2026, 12, 31)

    def __call__(self):
        return self.value


def time_tuple(year, month, day):
    return accounting.time.struct_time((year, month, day, 12, 0, 0, 0, 1, -1))


clock = Clock()
old_localtime = accounting.time.localtime
accounting.time.localtime = clock
app._stamp_today()
shown = app.fdate.get_text()
clock.value = time_tuple(2027, 1, 1)
app.f_desc.set_text("Year boundary")
app.f_amt.set_text("1.00")
app._on_add()
accounting.time.localtime = old_localtime
saved = app.tx[-1]
check("the visible date and persisted ISO date are one snapshot",
      (shown, saved.get("date"), saved.get("iso")) ==
      ("31 Dec", "31 Dec", "2026-12-31"),
      (shown, saved.get("date"), saved.get("iso")))

# ...and a form left open ACROSS midnight does not stamp yesterday.
# `_stamp_today`'s docstring promises "a long-open window never stamps a new
# entry with a stale day", and it was only ever called at the moment the form
# was revealed. Measured before the fix: opened 23:59 on 31 Dec, committed 00:01
# on 1 Jan, stored date='31 Dec' iso='2026-12-31'.
#
# The repair belongs on the LABEL, not at commit: re-stamping only when the
# entry is committed would store 1 Jan while the form on screen still read
# 31 Dec, which is the very desynchronisation the check above exists to forbid.
# A once-a-minute tick keeps both together. It is periodic rather than a single
# timer armed for midnight because this is a laptop OS that suspends, and a
# timer scheduled for a moment the machine spends asleep never fires.
clock2 = Clock()
accounting.time.localtime = clock2
app._toggle_form()          # closed by the Escape section? re-open cleanly
if not app.form_reveal.get_reveal_child():
    app._toggle_form()
pump()
opened_showing = app.fdate.get_text()
clock2.value = time_tuple(2027, 1, 1)
app._tick_form_clock()      # the minute tick the open form arms for itself
pump()
rolled_showing = app.fdate.get_text()
app.f_desc.set_text("Late night coffee")
app.f_amt.set_text("3.50")
app._on_add()
pump()
accounting.time.localtime = old_localtime
late = app.tx[-1]
check("a form open across midnight shows the new day",
      (opened_showing, rolled_showing) == ("31 Dec", "1 Jan"),
      (opened_showing, rolled_showing))
check("...and commits the new day, label and ISO still agreeing",
      (late.get("date"), late.get("iso")) == ("1 Jan", "2027-01-01"),
      (late.get("date"), late.get("iso")))
check("...and the open form keeps its clock armed",
      getattr(app, "_form_clock", None) is not None,
      getattr(app, "_form_clock", None))
app._toggle_form()
pump()
check("closing the form retires the clock",
      getattr(app, "_form_clock", None) is None,
      getattr(app, "_form_clock", None))

# Leap day is formatted without locale-dependent month text.
accounting.time.localtime = lambda: time_tuple(2028, 2, 29)
app._stamp_today()
accounting.time.localtime = old_localtime
check("leap day stamps both display and ISO", app.fdate.get_text() == "29 Feb"
      and app._form_iso == "2028-02-29", (app.fdate.get_text(), app._form_iso))

# Public callers cannot inject a figure that the UI rejects.
before = list(app.tx)
results = [app.add_entry("bad", v) for v in (0, float("nan"), float("inf"))]
check("programmatic entry rejects zero and non-finite figures",
      app.tx == before and results == [False, False, False], results)

# Edit every exposed field while retaining an entry's opaque identity metadata.
app.tx = [{"date": "2 Aug", "iso": "2026-08-02", "desc": "Rent",
           "amt": -950.0, "entry_id": "rent-7", "reconciled": True}]
app._refresh()
app._edit_tx(0)
pump()
app._e_desc.set_text("Rent revised")
app._e_amt.set_text("951.00")
app._save_edit()
pump()
edited = app.tx[0]
check("editing preserves transaction identity metadata",
      edited.get("entry_id") == "rent-7" and edited.get("reconciled") is True,
      edited)
check("edited fields and running balance refresh", edited["desc"] == "Rent revised"
      and edited["amt"] == -951.0
      and app.balance.get_text() == app._money(49.0),
      (edited, app.balance.get_text()))

# Both visible cancellation routes leave the ledger untouched.
before = [dict(t) for t in app.tx]
app._confirm_delete(0)
pump()
cancel = [b for b in buttons(app._confirm_layer) if b.get_label() == "Cancel"]
cancel[0].clicked()
pump()
check("Cancel dismisses confirmation without deleting", app.tx == before
      and app._confirm_layer is None, app.tx)

app._confirm_delete(0)
pump()
event = type("Event", (), {"keyval": Gdk.KEY_Escape})()
handled = app._on_key(app, event)
pump()
check("Escape dismisses confirmation without deleting", handled and app.tx == before
      and app._confirm_layer is None, app.tx)

# The editor underneath a confirmation must also be removed after deletion.
app._edit_tx(0)
app._confirm_delete(0)
pump()
app._do_confirmed_delete()
pump()
check("confirming delete from the editor removes exactly that row",
      app.tx == [] and app._confirm_layer is None and app._edit_layer is None,
      app.tx)

off.destroy()
app.destroy()
pump()
print("\n%d checks, %d failed" % (len(R), len(R) - sum(R)))
if not all(R):
    raise SystemExit(1)
print("all checks passed")
