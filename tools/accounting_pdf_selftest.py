#!/usr/bin/env python3
"""The exported ledger PDF, measured as geometry rather than read as code.

    tools/guestrun.sh python3 tools/accounting_pdf_selftest.py

`_render_pdf` and its five helpers (`text_at`, `right_at`, `desc_room`,
`table_header`, `_pdf_name`) were named by NO suite in this repo. It is a
multi-page document renderer for the one artefact this app produces that leaves
the machine — the thing you hand to a landlord, an accountant or a tax office —
and nothing checked that the ink lands where it should.

HOW THIS MEASURES. `_show_text` is wrapped so every draw is recorded with its
real Pango extents, then the drawn boxes are checked for two things a reader
cannot check by reading: whether any two on the same baseline OVERLAP, and
whether any lands outside the printable area. Page boundaries are derived from
the draw stream (within a page the baseline only ever moves down), because
cairo.PDFSurface is a C type that cannot be subclassed to count show_page.

THE DEFECT THIS WAS WRITTEN FOR. The date column is 58pt and the string in it
was bounded by nothing: `date` round-trips from the file verbatim, and the row
editor accepts a retyped date INCLUDING A YEAR on purpose (`_short_date_parts`
documents that). The screen ellipsizes this column — it was hardened after a
690-character date took the window's minimum width to 5309px — but the PDF drew
str(t["date"]) raw. Measured, through the plain interface: typing
"26 September 2026" into the row editor stored it verbatim and the export drew
it 81pt wide, ending at x=135 against a description starting at x=112, straight
through the user's own words. A 690-character date measured 3320pt on a 612pt
page. Fixed by giving the date the same `fit()` the description already used.

WHAT IS DELIBERATELY NOT CHECKED HERE. The export draws the WHOLE ledger while a
find query is active, and it draws the balance chart even when View ▸ Hide
Balance Chart is off. Both were measured and both are left alone on purpose: a
running-balance column and a closing balance are meaningless on a filtered
subset (the report card handles a query by reporting a NET instead, which is the
right answer for a summary and the wrong one for a ledger), and the View menu is
scoped to the window, not to a document written to Documents. Changing either
would be inventing a behaviour nobody asked for.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY of
accounting.py — never to the file itself:

  1. the date loses its bound (`fit(str(t["date"]), 9.5, DATE_W)` ->
     `str(t["date"])`)
       FAIL a date with a spelled-out month stays in its column
            <- '26 September 2026' (81pt) ends at x=135.0,
               'Quarterly settlement, ' starts at x=112.0
       FAIL a CJK date stays in its column
            <- '一月二十三日' (60pt) ends at x=114.0, ... starts at x=112.0
       FAIL a 690-character date stays in its column
            <- '6 September September September Septembe' (3320pt) ends at
               x=3374.0, ... starts at x=112.0
  2. pagination stops breaking (`if y + row_h > PH - MB:` -> `if False:`)
       FAIL every row sits inside the bottom margin
            <- '01 Aug' at y=750.0, limit 736.0
       FAIL every page carries a column header   <- 1 header(s) for 2 page(s)
  3. the header is not repeated after a break (`y = table_header(MT)` ->
     `y = MT`)
       FAIL every page carries a column header   <- 1 header(s) for 4 page(s)
  4. the running balance stops accumulating (`bal = round(bal + t["amt"], 2)`
     -> `bal = round(t["amt"], 2)`)
       FAIL every row prints the running balance to that point
            <- row 0 printed '-$1.00', the running total is '$999.00'

     THIS PROOF FOUND A HOLE IN THIS FILE. On its first run all 32 checks stayed
     GREEN with every row of the BALANCE column printing -$1.00, because the only
     balance being checked was the FOOTER's — and the footer is computed by a
     separate line (`total = round(self.opening + sum(...))`) that the mutation
     did not touch. Two independent computations, one of them guarded. The
     per-row check was added because of this, not before it.
"""
import os
import sys
import json
import shutil

H = "/tmp/nbhome-acctpdf-%d" % os.getpid()
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
import cairo                                                  # noqa: E402
import accounting                                             # noqa: E402

R = []

# US Letter, and the margins _render_pdf lays out against.
PW, PH = 612.0, 792.0
ML, MR, MT, MB = 54.0, 54.0, 64.0, 56.0
DESC_X = ML + 58          # where the description column starts

DRAWS = []
_real_show = accounting._show_text
_real_w = accounting._text_w


def _spy(cr, x, y, text, size, bold=False):
    DRAWS.append({"x": x, "y": y, "w": _real_w(cr, text, size, bold),
                  "text": text, "size": size, "page": 0})
    return _real_show(cr, x, y, text, size, bold)


accounting._show_text = _spy


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def build(tx, opening=0.0):
    with open(STORE, "w") as f:
        json.dump({"opening": opening, "tx": tx}, f)
    a = accounting.Accounting()
    pump()
    return a


def render(app, tag):
    """Render to a real PDF and return the recorded draws, page-numbered."""
    DRAWS[:] = []
    app._render_pdf("%s/%s.pdf" % (H, tag))
    page, last = 0, -1.0
    for d in DRAWS:
        if d["y"] < last - 1.0:
            page += 1
        last = d["y"]
        d["page"] = page
    return list(DRAWS), page + 1


def overlaps(draws):
    """Pairs on one baseline whose drawn boxes intersect."""
    bad = []
    rows = {}
    for d in draws:
        rows.setdefault((d["page"], round(d["y"], 1)), []).append(d)
    for (pg, y), row in sorted(rows.items()):
        row = sorted(row, key=lambda d: d["x"])
        for a, b in zip(row, row[1:]):
            if a["x"] + a["w"] > b["x"] + 0.5:
                bad.append("%r (%.0fpt) ends at x=%.1f, %r starts at x=%.1f"
                           % (a["text"][:40], a["w"], a["x"] + a["w"],
                              b["text"][:22], b["x"]))
    return bad


def E(desc, amt, date="01 Aug", iso="2026-08-01"):
    return {"date": date, "iso": iso, "desc": desc, "amt": amt}


DESC = "Quarterly settlement, west building"

# ------------------------------------------------------- the date column bound
# Reachable through the plain interface: the row editor takes a retyped date.
app = build([E("Rent", -950.0)], opening=0.0)
app._edit_tx(0)
pump()
app._e_date.set_text("26 September 2026")
app._save_edit()
pump()
check("a date retyped in the row editor is stored verbatim",
      app.tx[0]["date"] == "26 September 2026", app.tx[0]["date"])
app.destroy()
pump()

for tag, date in (("a date with a spelled-out month", "26 September 2026"),
                  ("a CJK date", "一月二十三日"),
                  ("a 690-character date", "6 " + "September " * 69)):
    app = build([E(DESC, -216.4, date=date)], opening=0.0)
    draws, _pages = render(app, "date")
    bad = overlaps(draws)
    check("%s stays in its column" % tag, not bad, bad[0] if bad else "")
    # The date must still be DRAWN, and what is drawn must be the beginning of
    # the real one: bounding the column may truncate it (with the ellipsis every
    # other truncation in this OS uses) but must never drop or garble it. Asking
    # for the full string instead would fail on any date wide enough to need the
    # bound, which is every date this section exists to test.
    col = [d["text"] for d in draws if abs(d["x"] - ML) < 0.5]
    kept = [s for s in col if s and date.startswith(s.rstrip("…")) and
            s.rstrip("…")]
    check("...and it is drawn, as a prefix of the real date", bool(kept),
          "nothing in the date column is a prefix of %r; column held %r"
          % (date[:30], col))
    app.destroy()
    pump()

# The common case must survive the bound rather than be truncated by it.
app = build([E(DESC, -216.4, date="26 Sep 2026")], opening=0.0)
draws, _p = render(app, "shortyear")
check("a short date WITH a year is printed in full",
      any(d["text"] == "26 Sep 2026" for d in draws),
      [d["text"] for d in draws if "26" in d["text"]])
app.destroy()
pump()

# ---------------------------------------------- nothing outside the paper
LEDGER = [E("Rent", -950.0), E("Salary", 2400.0), E("Groceries", -51.4),
          E("Quarterly settlement for the west building including grounds "
            "maintenance and winter salt", -216.4),
          E("Property purchase", -1234567.89),
          E("Sale of the business", 9876543.21)]
app = build([dict(t) for t in LEDGER], opening=5000000.0)
draws, pages = render(app, "margins")
over = [d for d in draws if d["x"] + d["w"] > PW - MR + 0.5]
check("nothing is drawn past the right margin", not over,
      over and "%r ends at %.1f, limit %.1f"
      % (over[0]["text"][:40], over[0]["x"] + over[0]["w"], PW - MR))
left = [d for d in draws if d["x"] < ML - 0.5]
check("nothing is drawn left of the left margin", not left,
      left and "%r at x=%.1f" % (left[0]["text"][:40], left[0]["x"]))
check("no column collides with another on a real ledger",
      not overlaps(draws), (overlaps(draws) or [""])[0])
app.destroy()
pump()

# --------------------------------------------------------------- pagination
N = 120
app = build([E("Entry %d" % i, -1.0) for i in range(N)], opening=1000.0)
draws, pages = render(app, "pages")
low = [d for d in draws if d["y"] > PH - MB + 0.5]
check("every row sits inside the bottom margin", not low,
      low and "%r at y=%.1f, limit %.1f"
      % (low[0]["text"][:30], low[0]["y"], PH - MB))
check("a %d-entry ledger runs to more than one page" % N, pages > 1,
      "%d page" % pages)

seen = [d["text"] for d in draws]
missing = [i for i in range(N) if "Entry %d" % i not in seen]
dupes = [i for i in range(N) if seen.count("Entry %d" % i) > 1]
check("every entry reaches the paper", not missing,
      "missing %s" % (missing[:10],))
check("...and none is printed twice", not dupes, "duplicated %s" % (dupes[:10],))
check("every page carries a column header", seen.count("BALANCE") == pages,
      "%d header(s) for %d page(s)" % (seen.count("BALANCE"), pages))
check("no column collides with another across %d pages" % pages,
      not overlaps(draws), (overlaps(draws) or [""])[0])

# The footer figure is the ledger's real closing balance, summed independently.
want = app._cmoney(round(1000.0 + sum(t["amt"] for t in app.tx), 2))
tail = [d["text"] for d in draws][-4:]
check("the closing balance is the opening plus every entry", want in tail,
      "footer says %r, the ledger comes to %r" % (tail[-1:], want))

# ...and EVERY row's running balance, not only the footer's total. These are two
# independent computations in _render_pdf: the footer sums self.tx in one line,
# the column accumulates down the loop. Checking only the footer left the whole
# BALANCE column unguarded — measured: breaking the accumulator
# (`bal = round(bal + t["amt"], 2)` -> `bal = round(t["amt"], 2)`) printed -$1.00
# on all 120 rows and this suite stayed entirely green.
col = [d for d in draws
       if abs(d["x"] + d["w"] - (PW - MR)) < 1.0 and d["text"] != "BALANCE"]
run, wrong = 1000.0, None
for i, t in enumerate(app.tx):
    run = round(run + t["amt"], 2)
    if i >= len(col):
        wrong = "only %d balance figures drawn for %d entries" % (len(col), N)
        break
    if col[i]["text"] != app._cmoney(run):
        wrong = "row %d printed %r, the running total is %r" % (
            i, col[i]["text"], app._cmoney(run))
        break
check("every row prints the running balance to that point", wrong is None,
      wrong or "")
app.destroy()
pump()

# ------------------------------------------------------- degenerate ledgers
for tag, tx, opening in (("an empty ledger", [], 0.0),
                         ("a single entry", [E("Rent", -950.0)], 0.0),
                         ("a zero-value entry", [E("Correction", 0.0)], 0.0),
                         ("every balance identical",
                          [E("a", 0.0), E("b", 0.0)], 100.0)):
    app = build([dict(t) for t in tx], opening=opening)
    try:
        draws, pages = render(app, "deg")
        ok, why = True, ""
    except Exception as exc:                                  # noqa: BLE001
        ok, why, draws = False, "%s: %s" % (type(exc).__name__, exc), []
    check("%s exports without raising" % tag, ok, why)
    if ok:
        check("...and puts something on the page" % (), len(draws) >= 4,
              "%d draws" % len(draws))
    app.destroy()
    pump()

# An empty ledger says so rather than printing a bare table.
app = build([], opening=0.0)
draws, _p = render(app, "emptytext")
check("an empty ledger says it is empty",
      any("No entries" in d["text"] for d in draws),
      [d["text"] for d in draws])
app.destroy()
pump()

# ----------------------------------------------------------------- the file
app = build([E("Rent", -950.0)], opening=0.0)
check("the export names the file with today's date",
      app._pdf_name().startswith("ledger-") and app._pdf_name().endswith(".pdf"),
      app._pdf_name())
docs = os.path.join(H, "Documents")
app._export_pdf()
pump()
made = [f for f in os.listdir(docs) if f.endswith(".pdf")] \
    if os.path.isdir(docs) else []
check("Export to PDF writes a file into Documents", bool(made), repr(made))
if made:
    size = os.path.getsize(os.path.join(docs, made[0]))
    check("...and it is a real PDF, not an empty file", size > 800,
          "%d bytes" % size)
    with open(os.path.join(docs, made[0]), "rb") as fh:
        check("...with a PDF header", fh.read(5) == b"%PDF-", "bad magic")
app.destroy()
pump()

# --------------------------------------------- the printed page is the same page
# Print and Export share `_render_pdf`, so the paper the renderer draws must be
# the paper the spooler is told to use. `nbprint.print_document` takes a `media`
# argument and warns in its own docstring that an app which draws at a different
# size MUST pass it "or CUPS scales their pages to fit letter paper". Accounting
# passes none, which is correct ONLY while its page is Letter — and nothing
# tied those two facts together. If someone moves this renderer to A4, every
# printed statement is silently rescaled and the export still looks right.
import inspect                                                # noqa: E402
import nbprint                                                # noqa: E402
src = inspect.getsource(accounting.Accounting._render_pdf)
check("the renderer draws US Letter", "PW, PH = 612.0, 792.0" in src,
      "page size line not found in _render_pdf")
check("...which is the media Print defaults to",
      nbprint.DEFAULT_MEDIA == "Letter", nbprint.DEFAULT_MEDIA)
psrc = inspect.getsource(accounting.Accounting._print)
check("...so Print is entitled to omit an explicit media",
      "media=" not in psrc or "Letter" in psrc,
      "Print passes a media this renderer does not draw: %r" % psrc)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
