#!/usr/bin/env python3
"""The printed bill report — the copy that goes by the phone.

    tools/guestrun.sh python3 tools/bills_report_selftest.py

`_render_pdf` and `_write_export_pdf` were named by NO suite. The layout itself
belongs to the shared `nbprint.report_page`, so what is checked here is what
this app CHOOSES TO SAY: for every kind of bill, does the printed page carry the
facts a person needs in order to pay it on time?

TWO DEFECTS, both from one if/elif chain that conflated "where to send it" with
"what else to say about it".

  * A posted bill whose address had not been filled in printed NO POST-BY DATE.
    The deadline was emitted inside `if bill["address"]`, but it does not depend
    on knowing the address — the lead days are what set it. Measured:
    `due_info` returned post_by 2026-08-12 while the report said only
    "Due 17 Aug · Account ACC-1 · By post". The copy by the phone told somebody
    to post on the 17th a bill that had to be in the post by the 12th, which is
    the single thing this app exists to prevent.
  * A posted bill that HAD an address silently dropped its NOTE — the user's own
    words about that bill. Measured: a bill noting "Quote 88" printed the payee,
    the amount, the address and the deadline, and not the note.

HOW THIS MEASURES: `nbprint.report_page` is wrapped so every `text.emit` is
recorded, and the emitted lines are grouped per bill. That reads what the
renderer was ASKED to draw. It does not prove those glyphs reached the paper —
`nbprint` is campaign-owned and has its own coverage — so the last section also
writes a real PDF and checks the file is a PDF with plausible weight, which is
the boundary between this suite's responsibility and that one's.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the post-by line goes back inside the address check
     (`if info["post_by"]:` -> `if info["post_by"] and bill["address"]:`)
       FAIL a posted bill with no address still prints its deadline
            <- ['Due 17 Aug  ·  Account NOADDR  ·  By post']
  2. the note goes back on the address if/elif chain
     (`if bill["note"]:` -> `if bill["note"] and not bill["address"]:`)
       FAIL a posted bill with an address still prints its note
            <- 'Quote 88' missing from
               ['Due 17 Aug  ·  Account WITHADDR  ·  By post', 'Acme',
                'PO Box 1', 'Post by 12 August 2026']
"""
import os
import sys
import json
import time
import shutil

H = "/tmp/nbhome-billsrep-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)
STORE = H + "/.config/notebook/bills.json"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("BILLS_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402
import nbprint                                                # noqa: E402
import bills                                                  # noqa: E402

uishot.load_theme()
# bills sizes its detail column from the screen (bills.py:1034); offscreen that
# is the HOST monitor and the app builds a 1920 layout. Pin the guest panel.
nbapp.screen_size = lambda: (1024, 768)

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump(n=900):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def iso(days):
    return time.strftime("%Y-%m-%d",
                         time.localtime(time.time() + days * 86400))


# One bill of every shape the report branches on. `account` doubles as the tag
# the emitted lines are grouped by.
CASES = (
    ("WITHADDR", dict(method="mail", address="Acme\nPO Box 1", phone="",
                      note="Quote 88", lead=5)),
    ("NOADDR", dict(method="mail", address="", phone="", note="", lead=5)),
    ("NOADDRNOTE", dict(method="mail", address="", phone="", note="Ref 22",
                        lead=5)),
    ("NOLEAD", dict(method="mail", address="Acme\nBox 2", phone="", note="",
                    lead=0)),
    ("PHONE", dict(method="phone", address="", phone="555-0100",
                   note="Ask for billing", lead=5)),
    ("PERSON", dict(method="person", address="", phone="", note="Counter 3",
                    lead=5)),
    ("AUTO", dict(method="auto", address="", phone="", note="Direct debit",
                  lead=5)),
)


def build():
    rows = []
    for i, (tag, extra) in enumerate(CASES):
        b = dict(id="b%d" % i, payee="Payee %s" % tag, account=tag,
                 amount=1000 + i, due=iso(9), every=1, paid=[])
        b.update(extra)
        rows.append(b)
    with open(STORE, "w") as f:
        json.dump({"bills": rows}, f)
    a = bills.Bills()
    pump()
    return a


def emitted(app):
    """Every line the report asks nbprint to draw, grouped per bill."""
    lines = []
    real = nbprint.report_page

    def spy(path):
        surf, cr, text = real(path)
        real_emit = text.emit

        def emit(s, *a, **k):
            lines.append(s)
            return real_emit(s, *a, **k)
        text.emit = emit
        return surf, cr, text

    nbprint.report_page = spy
    try:
        app._render_pdf(os.path.join(H, "probe.pdf"))
    finally:
        nbprint.report_page = real

    # Match the payee EXACTLY. The headline is "<payee>   <amount>", and a
    # startswith() test grouped "Payee NOADDR" under "Payee NOADDRNOTE" because
    # one tag is a prefix of the other -- leaving the first bill with no lines
    # at all and the checks reading as an app defect.
    heads = dict(("Payee %s" % tag, tag) for tag, _e in CASES)
    groups, cur = {}, None
    for ln in lines:
        tag = heads.get(ln.split("   ")[0])
        if tag is not None:
            cur = tag
            groups[cur] = []
            continue
        if cur:
            groups[cur].append(ln)
    return groups


app = build()
g = emitted(app)

check("every bill reaches the report", len(g) == len(CASES),
      "%d of %d: %s" % (len(g), len(CASES), sorted(g)))

# ------------------------------------------------------- THE POST-BY DEADLINE
for tag in ("WITHADDR", "NOADDR", "NOADDRNOTE"):
    bill = [b for b in app.bills if b["account"] == tag][0]
    want = bills.due_info(bill)["post_by"]
    check("the model gives %s a post-by date" % tag, bool(want), want)
    check("a posted bill with %s still prints its deadline"
          % ("an address" if tag == "WITHADDR" else "no address"),
          any("Post by" in ln for ln in g.get(tag, [])), g.get(tag))

# A bill posted with NO lead days has no deadline to print, and must not invent
# one — the guard has to be the date's existence, not the address's.
bill = [b for b in app.bills if b["account"] == "NOLEAD"][0]
check("a posted bill with no lead days has no post-by date",
      not bills.due_info(bill)["post_by"], bills.due_info(bill)["post_by"])
check("...and the report does not invent one",
      not any("Post by" in ln for ln in g.get("NOLEAD", [])), g.get("NOLEAD"))

# Nor do the methods that are not posted.
for tag in ("PHONE", "PERSON", "AUTO"):
    check("a %s bill prints no post-by date" % tag.lower(),
          not any("Post by" in ln for ln in g.get(tag, [])), g.get(tag))

# --------------------------------------------------------------- THE NOTE
for tag, note in (("WITHADDR", "Quote 88"), ("NOADDRNOTE", "Ref 22"),
                  ("PHONE", "Ask for billing"), ("PERSON", "Counter 3"),
                  ("AUTO", "Direct debit")):
    check("a posted bill with %s still prints its note"
          % ("an address" if tag == "WITHADDR" else "method " + tag.lower()),
          any(note in ln for ln in g.get(tag, [])),
          "%r missing from %s" % (note, g.get(tag)))

# ------------------------------------------------------------ THE DUE DATE
# The printed page never carried one. `info["state"]` is written for a 252px
# rail and a desktop tile, so it prints "Overdue", "Due in 2 days" or a "Due 28
# Feb" with no year — measured, a phone bill and an automatic bill printed no
# date at all, and the copy that goes by the phone is the copy a person pays
# from. Every bill with something outstanding names the day it is due, in full.
for tag, _e in CASES:
    bill = [b for b in app.bills if b["account"] == tag][0]
    want = bills.fmt_date(bills.due_info(bill)["due"], year=True)
    check("%s's report line carries its due date in full" % tag,
          any(want in ln for ln in g.get(tag, [])),
          "%r missing from %s" % (want, g.get(tag)))

# ------------------------------------------------ the facts that must be there
for tag, _e in CASES:
    lines = g.get(tag, [])
    check("%s's report line names its account" % tag,
          any(tag in ln for ln in lines), lines)
check("a posted bill prints its address",
      any("PO Box 1" in ln for ln in g.get("WITHADDR", [])), g.get("WITHADDR"))
check("a phone bill prints its number",
      any("555-0100" in ln for ln in g.get("PHONE", [])), g.get("PHONE"))

# --------------------------------------------------- and a real file is written
docs = os.path.join(H, "Documents")
app._write_export_pdf()
pump()
made = [f for f in os.listdir(docs) if f.endswith(".pdf")] \
    if os.path.isdir(docs) else []
check("Export writes a PDF into Documents", bool(made), repr(made))
if made:
    path = os.path.join(docs, made[0])
    check("...and it is a real PDF, not an empty file",
          os.path.getsize(path) > 800, "%d bytes" % os.path.getsize(path))
    with open(path, "rb") as fh:
        check("...with a PDF header", fh.read(5) == b"%PDF-", "bad magic")

# A bill that is already late keeps the word that says so, in front of the date
# — and a bill with nothing outstanding has no date to print, so it says what it
# does have.
with open(STORE, "w") as f:
    json.dump({"bills": [
        dict(id="late", payee="Payee LATE", account="LATE", amount=2500,
             due="2026-01-20", every=0, method="phone", address="",
             phone="555-0199", note="", lead=0, paid=[]),
        dict(id="done", payee="Payee DONE", account="DONE", amount=2500,
             due="2026-01-20", every=0, method="phone", address="",
             phone="555-0199", note="", lead=0,
             paid=[{"on": "2026-01-19", "for": "2026-01-20"}])]}, f)
app4 = bills.Bills()
pump()
lines4 = []
_real_page = nbprint.report_page


def _spy4(path):
    surf, cr, text = _real_page(path)
    _real_emit = text.emit

    def emit(s, *a, **k):
        lines4.append(s)
        return _real_emit(s, *a, **k)
    text.emit = emit
    return surf, cr, text


nbprint.report_page = _spy4
try:
    app4._render_pdf(os.path.join(H, "late.pdf"))
finally:
    nbprint.report_page = _real_page
check("an overdue bill prints the word AND the date it was due",
      any("Overdue" in ln and "20 January 2026" in ln for ln in lines4),
      lines4)
check("a settled bill prints what it has instead of a date",
      any(ln.startswith("Paid") for ln in lines4)
      and not any("Paid" in ln and "January" in ln for ln in lines4), lines4)
app4.destroy()
pump()

# An empty book still produces a page that says so, rather than a blank sheet.
with open(STORE, "w") as f:
    json.dump({"bills": []}, f)
app2 = bills.Bills()
pump()
g2 = emitted(app2)
check("an empty book still renders without raising", True)
app.destroy()
app2.destroy()
pump()

# ------------------------------------------ when writing the report FAILS
# Repaired by the bug-fix lane's error-honesty audit (HANDOFF, 2026-08-09) and
# left unguarded. Both paths matter for the same reason the messaging axis does:
# an app may do less than hoped, but it must never leave the person with nothing
# to read, and it must never hand them the operating system's own words.
import nbprint as _nbprint                                    # noqa: E402


def flashes(app):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            t = (w.get_text() or "").strip()
            if t:
                out.append(t)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    walk(app)
    return out


app3 = build()
real_render = app3._render_pdf


def _enospc(path):
    raise OSError(28, "No space left on device", path)


app3._render_pdf = _enospc
app3._write_export_pdf()
pump()
said = flashes(app3)
check("a full disk is reported to the person",
      any("disk is full" in t.lower() for t in said), said[-3:])
check("...in the OS's words about DISKS, not the OS's error string",
      not any("No space left on device" in t or "Errno" in t for t in said),
      [t for t in said if "Errno" in t or "No space left" in t])
app3._render_pdf = real_render

real_print = _nbprint.print_document


def _noprinter(*_a, **_k):
    raise FileNotFoundError(2, "No such file or directory", "lp")


_nbprint.print_document = _noprinter
app3._print()
pump()
said = flashes(app3)
check("a missing spooler is reported rather than raised",
      any("Print failed" in t for t in said), said[-3:])
check("...without the exception leaking through",
      not any("No such file" in t or "Errno" in t for t in said),
      [t for t in said if "Errno" in t or "No such file" in t])
_nbprint.print_document = real_print
check("...and the app is still usable afterwards", len(app3.bills) >= 1,
      len(app3.bills))
app3.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
