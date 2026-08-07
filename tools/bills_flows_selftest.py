#!/usr/bin/env python3
"""
Adding, paying, deleting and exporting a bill.

`bills_selftest` covers the data model. Measured with a function-level profiler
over a real run of it, **25 of bills.py's 76 functions were never entered** —
and they are the user-facing half: `_open_form` and its commit, `_open_payment`
and its commit, `_confirm_delete`, `_do_delete`, `_render_pdf`, `_export_pdf`,
`_print`, `_on_key`. Everything you can actually do to a bill.

The gap had a defect in it. `_export_pdf` wrote `Documents/Bills.pdf` at a fixed
name with no question asked, so a second export destroyed the first — and
destroyed anything else in Documents with that name. ROADMAP #5 fixed exactly
this in Journal, Novel, Cookbook and Academics; bills was not on the list and
kept it. It is the only unguarded fixed-path export left in the tree.

WHY A DECOY. Asserting that the export "wrote a file" passes whether or not it
asked first. This plants known bytes at the real destination and reads them back
after: DECLINE must leave them untouched, ACCEPT must replace them, and a fresh
name must be written with no question at all.

Run:
    tools/guestrun.sh python3 tools/bills_flows_selftest.py
    tools/guestrun.sh python3 tools/bills_flows_selftest.py --de DIR
"""
import os
import sys
import json
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-bills-")
os.environ["NB_HOME"] = _HOME
os.makedirs(os.path.join(_HOME, "Documents"), exist_ok=True)

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import bills as B  # noqa: E402

FAILED, N = [], [0]
DECOY = b"%PDF-1.4 a file the user already had\n"


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


def widgets(root, kind, out=None):
    out = [] if out is None else out
    if isinstance(root, kind):
        out.append(root)
    if isinstance(root, Gtk.Container):
        for k in root.get_children():
            widgets(k, kind, out)
    return out


def overlay_buttons(app):
    """The buttons on whatever card is currently over the app.

    `_overlay_card`, not `_overlay`: the latter is the Gtk.Overlay wrapping the
    ENTIRE window, so walking it finds the menu bar's buttons and every row in
    the list. That made "is a question being asked?" true at all times, and
    press() clicked whichever menu item happened to share a label with the one
    the card wanted (measured: it did both)."""
    card = getattr(app, "_overlay_card", None)
    if card is None:
        return []
    return [b for b in widgets(card, Gtk.Button) if b.get_label()]


def press(app, label):
    for b in overlay_buttons(app):
        if b.get_label() == label:
            b.clicked()
            pump()
            return True
    return False


def overlay_text(app):
    card = getattr(app, "_overlay_card", None)
    if card is None:
        return ""
    return " ".join(" ".join(l.get_text().split())
                    for l in widgets(card, Gtk.Label))


def saved():
    p = os.path.join(_HOME, ".config", "notebook", "bills.json")
    try:
        with open(p) as fh:
            return json.load(fh)
    except Exception:
        return {}


def main():
    app = B.Bills()
    pump()
    before = len(app.bills)

    # ---- add a bill, through the real sheet ------------------------------
    app._open_form(None)
    pump()
    ents = widgets(getattr(app, "_overlay_card", None) or app, Gtk.Entry)
    opened = check("the add-a-bill sheet opens", bool(ents),
                   "%d entries" % len(ents))
    if not opened:
        not_reached("no sheet", "adding a bill keeps it")
        return 1
    ents[0].set_text("Waterworks")
    for e in ents[1:]:
        if not e.get_text():
            e.set_text("48.20")
            break
    pressed = press(app, "Add Bill") or press(app, "Save")
    pump()
    added = check("the sheet commits a new bill",
                  pressed and len(app.bills) == before + 1,
                  "pressed=%s n=%d" % (pressed, len(app.bills)))
    if added:
        check("...and it is on disk", any(b.get("payee") == "Waterworks"
                                          for b in saved().get("bills", [])))
    else:
        not_reached("nothing was added", "...and it is on disk")

    bill = next((b for b in app.bills if b["payee"] == "Waterworks"), None)
    if bill is None:
        bill = app.bills[0] if app.bills else None
    if bill is None:
        not_reached("no bill to work with", "a payment is recorded",
                    "the delete confirm is raised", "Cancel keeps the bill")
        return 1

    # ---- record a payment -------------------------------------------------
    # The whole point of the app is the POST BY date, and a payment is what
    # moves a bill on to the next one.
    was = len(bill.get("paid") or [])
    due_before = B.due_info(bill)["due"]
    app._open_payment(bill["id"])
    pump()
    raised = check("the payment sheet opens", bool(overlay_buttons(app)))
    if raised:
        check("...and says which occurrence it settles",
              "settle" in overlay_text(app).lower()
              or "outstanding" in overlay_text(app).lower(),
              overlay_text(app)[:80])
        press(app, "Record Payment")
        pump()
        now = len(app._bill(bill["id"])["paid"] or [])
        rec = check("a payment is recorded", now == was + 1,
                    "%d -> %d" % (was, now))
        if rec:
            check("...and it is on disk",
                  any(len(b.get("paid") or []) == now
                      for b in saved().get("bills", [])
                      if b["id"] == bill["id"]))
            due_after = B.due_info(app._bill(bill["id"]))["due"]
            check("...and the bill moves on to its next due date",
                  due_after != due_before or due_before is None,
                  "%r -> %r" % (due_before, due_after))
        else:
            not_reached("no payment", "...and it is on disk",
                        "...and the bill moves on to its next due date")
    else:
        not_reached("no payment sheet", "...and says which occurrence it settles",
                    "a payment is recorded", "...and it is on disk",
                    "...and the bill moves on to its next due date")

    # ---- delete is immediately undoable ------------------------------------
    n_before = len(app.bills)
    history_before_delete = len((app._bill(bill["id"]) or {}).get("paid") or [])
    app._do_delete(bill["id"])
    pump()
    removed = check("Delete removes it", len(app.bills) == n_before - 1,
                    "%d bills" % len(app.bills))
    check("...and from disk",
          not any(b["id"] == bill["id"] for b in saved().get("bills", [])))
    undo = getattr(app, "_undo_delete", None)
    if removed and callable(undo):
        undo()
        pump()
        restored = app._bill(bill["id"])
        check("deletion has Undo", restored is not None,
              "%d bills" % len(app.bills))
        check("...which restores payment history",
              restored is not None
              and len(restored.get("paid") or []) == history_before_delete,
              repr(restored.get("paid") if restored else None))
        check("...and persists the restored bill",
              any(b["id"] == bill["id"] for b in saved().get("bills", [])))
    else:
        not_reached("no undo", "deletion has Undo",
                    "...which restores payment history",
                    "...and persists the restored bill")

    # ---- export -----------------------------------------------------------
    if not app.bills:
        app._open_form(None)
        pump()
        e = widgets(getattr(app, "_overlay_card", None) or app, Gtk.Entry)
        if e:
            e[0].set_text("Gas")
            press(app, "Add Bill") or press(app, "Save")
            pump()
    dest = os.path.join(_HOME, "Documents", B.PDF_NAME)

    # A fresh name: no question, just the file.
    if os.path.exists(dest):
        os.remove(dest)
    app._export_pdf()
    pump()
    fresh = check("exporting to a free name just writes it",
                  os.path.exists(dest) and not overlay_buttons(app),
                  "exists=%s asked=%s" % (os.path.exists(dest),
                                          bool(overlay_buttons(app))))

    # Now something is there. Decline, and it must survive byte for byte.
    with open(dest, "wb") as fh:
        fh.write(DECOY)
    app._export_pdf()
    pump()
    asked = check("exporting over an existing file asks first",
                  bool(overlay_buttons(app)),
                  overlay_text(app)[:80])
    if asked:
        check("...naming the file it would replace",
              B.PDF_NAME in overlay_text(app), overlay_text(app)[:80])
        focus = app.get_focus()
        check("...with Cancel focused",
              isinstance(focus, Gtk.Button)
              and (focus.get_label() or "").lower().startswith("cancel"))
        press(app, "Cancel")
        pump()
        with open(dest, "rb") as fh:
            after = fh.read()
        check("declining leaves the file exactly as it was", after == DECOY,
              repr(after[:32]))

        app._export_pdf()
        pump()
        press(app, "Replace")
        pump()
        with open(dest, "rb") as fh:
            after = fh.read()
        check("accepting replaces it with a real PDF",
              after != DECOY and after.startswith(b"%PDF"),
              repr(after[:16]))
    else:
        not_reached("no question was asked",
                    "...naming the file it would replace",
                    "...with Cancel focused",
                    "declining leaves the file exactly as it was",
                    "accepting replaces it with a real PDF")
        # It wrote anyway: say what was lost.
        with open(dest, "rb") as fh:
            check("the decoy survived  [it did not]", fh.read() == DECOY)

    try:
        app.destroy()
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
