#!/usr/bin/env python3
"""Do the ledger's column headings sit over their own figures?

    tools/guestrun.sh python3 tools/accounting_columns_selftest.py

A money table whose heading is not above its column is misread, and the
scrollbar makes the error conditional: the rows lose width to it and the header
does not, so the columns drift apart exactly when the ledger has grown long
enough for the alignment to matter. `_sync_head_gutter` exists to prevent that
and had never been named by any suite.

THE DEFECT. It reserved `vsb.get_allocated_width()`. Measured at 1024x722 with
200 rows, that is 17px — but the viewport gives up 20px, the extra 3px being CSS
spacing that a widget's allocation does not report. So every money heading sat
3px to the right of its figures whenever the ledger scrolled, and 1px off when
it did not. Fixed by measuring the space the rows actually LOSE (the scrolled
window's width less its child's) instead of the scrollbar's own width.

HOW THIS MEASURES. Right edges in the OFFSCREEN WINDOW's coordinates, via
translate_coordinates. Comparing allocations directly is meaningless here
because the header and the rows have different parents — that mistake produced a
false "311px MISALIGNED" earlier in this campaign. Cells are found by STYLE
CLASS (txdebit / txcredit / txbal), never by "looks like a number": a
description reading "Entry 199" contains digits, and a row with no debit has one
money cell fewer, which between them produced a false 117px drift the first time
this was measured.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY of
accounting.py:

  1. the gutter reserves the scrollbar's own width again — i.e. the defect this
     file was written for
     (`w = max(0, scrolled.get_allocated_width() - vp.get_allocated_width())`
      -> `w = vsb.get_allocated_width() if ... else 0`)          5 FAILED
       FAIL the header reserves exactly the width the rows lost when a short
            ledger              <- reserved 1px, the rows lost 0px
       FAIL the header reserves exactly the width the rows lost when the ledger
            scrolls             <- reserved 17px, the rows lost 20px
       FAIL DEBIT sits over its figures when the ledger scrolls
            <- heading right edge 701, figure right edge 698
       FAIL CREDIT sits over its figures when the ledger scrolls
            <- heading right edge 819, figure right edge 816
       FAIL BALANCE sits over its figures when the ledger scrolls
            <- heading right edge 959, figure right edge 956
  2. the header reserves no gutter at all (`w = 0`)                4 FAILED
       FAIL the header reserves exactly the width the rows lost when the ledger
            scrolls             <- reserved 0px, the rows lost 20px
       FAIL BALANCE sits over its figures when the ledger scrolls
            <- heading right edge 976, figure right edge 956
       (DEBIT and CREDIT likewise, each 20px out)

Note both proofs leave the SHORT-ledger column checks green: with no scrollbar
there is nothing to reserve and the columns line up either way. That is why the
scrolling case is built at 200 entries and asserted to really take a scrollbar
before its alignment is read — without that assertion this whole file would pass
vacuously the day the row limit or the panel height changed.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctcols-%d" % os.getpid()
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
import uishot                                                 # noqa: E402
import accounting                                             # noqa: E402

uishot.load_theme()

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=800):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def build(n):
    """A ledger of n entries, alternating debit and credit so a single row can
    never supply every money cell."""
    with open(STORE, "w") as f:
        json.dump({"opening": 1000.0,
                   "tx": [{"date": "0%d Aug" % (1 + i % 9),
                           "iso": "2026-08-%02d" % (1 + i % 28),
                           "desc": "Entry %d" % i,
                           "amt": -12.34 if i % 2 else 250.0}
                          for i in range(n)]}, f)
    a = accounting.Accounting()
    pump()
    return a


def show(app, w=1024, h=722):
    """Realise the tree the way the desktop does. Without this every child
    reports a zero allocation and every measurement below is meaningless."""
    off = Gtk.OffscreenWindow()
    kid = app.get_child()
    app.remove(kid)
    off.add(kid)
    off.set_size_request(w, h)
    off.show_all()
    pump()
    off.get_pixbuf()
    pump()
    return off


def labels(container):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            out.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    walk(container)
    return out


def right_edge(off, w):
    a = w.get_allocation()
    r = w.translate_coordinates(off, a.width, 0)
    return None if r is None else r[0]


COLUMNS = (("DEBIT", "txdebit"), ("CREDIT", "txcredit"),
           ("BALANCE", "txbal"))

for n, tag, scrolls in ((4, "a short ledger", False),
                        (200, "the ledger scrolls", True)):
    app = build(n)
    off = show(app)

    heads = dict((l.get_text().upper(), right_edge(off, l))
                 for l in labels(app._ledgerhead) if (l.get_text() or "").strip())
    rows = [w for w in app.rows.get_children() if isinstance(w, Gtk.Button)]
    check("%s builds its rows" % tag, bool(rows), "%d rows" % len(rows))

    vsb = app._rowscroll.get_vscrollbar()
    vp = app._rowscroll.get_child()
    lost = app._rowscroll.get_allocated_width() - vp.get_allocated_width()
    if scrolls:
        check("a %d-entry ledger really does take a scrollbar" % n, lost > 0,
              "the rows lost %dpx to it" % lost)
    check("the header reserves exactly the width the rows lost when %s" % tag,
          app._hdr_gutter == max(0, lost),
          "reserved %dpx, the rows lost %dpx" % (app._hdr_gutter, lost))

    for name, cls in COLUMNS:
        he = heads.get(name)
        fig = None
        for r in rows[:4]:
            for l in labels(r):
                if l.get_style_context().has_class(cls) and \
                        (l.get_text() or "").strip():
                    fig = (l.get_text(), right_edge(off, l))
                    break
            if fig:
                break
        if he is None or fig is None or fig[1] is None:
            check("%s has a heading and a figure to compare when %s"
                  % (name, tag), False, "heading=%s figure=%s" % (he, fig))
            continue
        check("%s sits over its figures when %s" % (name, tag),
              abs(he - fig[1]) <= 1,
              "heading right edge %s, figure right edge %s" % (he, fig[1]))

    off.destroy()
    app.destroy()
    pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
