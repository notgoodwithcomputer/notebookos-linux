#!/usr/bin/env python3
"""The day/month/year picker gives back the date that was picked.

    tools/guestrun.sh python3 tools/bills_dates_selftest.py

`_date_row` and `_date_of` were named by no suite. `_date_row`'s own docstring
says why they exist: "a date entered as text is a date that can be entered
wrongly, and the one field a bill cannot afford to have wrong is when it is
due." Three controls instead of a typed string removes one class of error and
introduces two others — an off-by-one on the month index, and a day that does
not exist in the month it was left in.

THE TRANSLATION TRAP, which is the reason this file runs in three languages.
`nbi18n` translates a combo's items, so the text read back from a month combo is
the TRANSLATION and never matches an English month name. `_date_of` reads
`get_active()` — the index — and the comment beside it says so. That is the
right answer, and it is exactly the kind of correct-for-a-reason line that a
later edit "simplifies" into `get_active_text()`, at which point every language
but English silently picks the wrong month. Measured here at index 11 in en, de
and ru: all three must give month 12.

MEASURED, NOTHING WAS WRONG. Day clamping, leap years, month indices and the
spinner's own bounds are all correct in all three languages. This file pins
behaviour that already holds; the red proofs are the evidence it is able to
fail.

RED PROOFS (M1), measured, each mutation applied ALONE to a scratch COPY:

  1. the day is not clamped into its month
     (`day = max(1, min(int(d.get_value()), _month_len(year, month)))`
      -> `day = max(1, int(d.get_value()))`)                    6 FAILED
       FAIL 31 in a non-leap February clamps to 28  <- 2026-02-31
       FAIL 31 in a LEAP February clamps to 29      <- 2028-02-31
       FAIL 2100 is not a leap year                 <- 2100-02-31
       FAIL 2000 IS a leap year                     <- 2000-02-31
       FAIL 31 in a 30-day month clamps to 30       <- 2026-04-31
       FAIL 31 June clamps to 30                    <- 2026-06-31
  2. the month index is read off by one
     (`month = max(1, min(12, m.get_active() + 1))`
      -> `month = max(1, min(12, m.get_active()))`)            13 FAILED
       FAIL the picker opens on the date it was handed   <- 2026-07-15
       FAIL December is month 12, not 11                 <- 2026-11-01
       FAIL all twelve month indices map to their own month
       FAIL the same index means the same month in every language
       ...and every dated case shifted a month back.
     Run under BILLS_DATES_LANG=de and BILLS_DATES_LANG=ru as well: the same
     13 fail, which is the point — the index path must be language-blind, and a
     regression in it is not something an English-only run would catch late.
"""
import os
import sys
import json
import shutil

LANG = os.environ.get("BILLS_DATES_LANG")
H = "/tmp/nbhome-billsdates-%s-%d" % (LANG or "en", os.getpid())
os.environ["NB_HOME"] = H
if LANG:
    os.environ["NB_LANG"] = LANG
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook", exist_ok=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.environ.get("BILLS_MODULE_DIR", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")))

with open(H + "/.config/notebook/bills.json", "w") as f:
    json.dump({"bills": [dict(id="m", payee="Rent", account="", amount=5000,
                              due="2026-08-15", every=1, method="phone",
                              address="", phone="", note="", lead=0,
                              paid=[])]}, f)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402
import bills                                                  # noqa: E402

uishot.load_theme()
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


app = bills.Bills()
pump()
_row, parts = app._date_row("Due", "2026-08-15")
d, m, y = parts


def pick(year, month, day):
    y.set_value(year)
    m.set_active(month - 1)
    d.set_value(day)
    return app._date_of(parts)


# The picker must OPEN on the date it was given, or every edit silently moves
# the bill to whatever the controls happened to default to.
check("the picker opens on the date it was handed",
      app._date_of(parts) == "2026-08-15", app._date_of(parts))

CASES = (
    (2026, 1, 31, "2026-01-31", "31 January stays 31"),
    (2026, 2, 31, "2026-02-28", "31 in a non-leap February clamps to 28"),
    (2028, 2, 31, "2028-02-29", "31 in a LEAP February clamps to 29"),
    (2100, 2, 31, "2100-02-28", "2100 is not a leap year"),
    (2000, 2, 31, "2000-02-29", "2000 IS a leap year"),
    (2026, 4, 31, "2026-04-30", "31 in a 30-day month clamps to 30"),
    (2026, 6, 31, "2026-06-30", "31 June clamps to 30"),
    (2026, 12, 31, "2026-12-31", "31 December stays"),
    (2026, 1, 1, "2026-01-01", "the first of January"),
    (2026, 12, 1, "2026-12-01", "December is month 12, not 11"),
    (1970, 6, 15, "1970-06-15", "the earliest year the spinner allows"),
    (2999, 6, 15, "2999-06-15", "the latest year the spinner allows"),
)
for year, month, day, want, why in CASES:
    got = pick(year, month, day)
    check(why, got == want, got)

# Every month index maps to its own month, in order, with no gap or repeat.
seen = [pick(2026, i + 1, 15)[5:7] for i in range(12)]
check("all twelve month indices map to their own month",
      seen == ["%02d" % i for i in range(1, 13)], seen)

# THE TRANSLATION TRAP. The combo's ITEMS are translated; its INDEX is not.
# Index 11 must be December whatever the interface language says on the button.
shown = [m.get_model()[i][0] for i in range(12)]
check("the month names are shown in the interface language",
      len(shown) == 12 and all(s for s in shown), shown[:3])
m.set_active(11)
d.set_value(1)
y.set_value(2026)
check("the same index means the same month in every language",
      app._date_of(parts) == "2026-12-01",
      "%s index 11 gave %s, expected 2026-12-01"
      % (LANG or "en", app._date_of(parts)))
check("...and the label at that index is not an English month name in a "
      "translated build",
      True if not LANG else shown[11] != "December",
      "%s showed %r at index 11" % (LANG, shown[11]))

# The spinner bounds are the app's promise about what can be entered at all.
lo, hi = d.get_range()
check("the day spinner is bounded to 1..31", (lo, hi) == (1, 31), (lo, hi))
lo, hi = y.get_range()
check("the year spinner is bounded", lo <= 1970 and hi >= 2999, (lo, hi))

app.destroy()
pump()

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
