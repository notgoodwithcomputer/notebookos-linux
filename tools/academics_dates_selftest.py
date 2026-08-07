#!/usr/bin/env python3
"""How Academics says a date out loud.

Every dated line in this app — a homework due date, a lecture in the sidebar —
goes through academics._pretty_due. Two defects lived in it, both found by
looking at the rendered app rather than by reading the function:

  * THE PAST WAS NEVER NAMED. `if days < 0: return "%d days ago"` caught every
    past date however old, so the dated form below it was unreachable looking
    backwards. A lecture taken last November was labelled "266 days ago" — a
    number nobody can turn back into a day.
  * THE MONTH WAS TRANSLATED ON ITS OWN and concatenated in English word order:
    "%d %s" % (d, _t(MONTH)). nbi18n does not merely translate a date, it
    REORDERS one, and _date_lookup only fires when the ENTIRE string is a date.
    Measured across the shipped catalogs, that produced "14 九月" where Chinese
    writes 9月14日, "14 9월" for Korean's 9월 14일, "14 Septiembre" for Spanish's
    "14 de septiembre", and a mid-sentence capital in French and Russian.

RED PROOFS (M1 — a gate nobody has watched fail is decoration). Each mutation
applied alone, with the output measured, not imagined:

  1. restore the unbounded past branch (`if -7 < days < 0:` -> `if days < 0:`)
       FAIL a lecture from last year is named, not counted   <- 270 days ago
       FAIL a date a month back is named, not counted        <- 40 days ago
       FAIL a week back is already too far to count          <- 7 days ago
       RESULT: 3 FAILED
  2. restore the month-only translation
     (`return _t(stamp)` -> `return "%d %s" % (d, _t(_MONTHS[m - 1]))`)
       FAIL zh writes the date the way zh writes dates
            <- '14 九月', expected the form nbi18n produces for a whole date
               ('9月14日')
       FAIL ja writes the date the way ja writes dates
            <- '14 9月', expected ... ('9月14日')
       ...and ko, es, fr, ru, plus the two year checks, which that line also
       drops. RESULT: 8 FAILED
  3. drop the year suffix (`stamp = "%s %d" % (stamp, y)` -> `pass`)
       FAIL a lecture from last year is named, not counted   <- 14 November
       FAIL a date in another year says which year           <- 9 March
       RESULT: 2 FAILED
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

H = "/tmp/nbhome-acaddates-%d" % os.getpid()
os.environ["NB_HOME"] = H
os.makedirs(H + "/.config/notebook", exist_ok=True)
sys.path.insert(0, DE)

import academics                                              # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


# A fixed "today" so this suite does not drift with the wall clock. Tuesday.
TODAY = "2026-08-11"
academics._today_key = lambda: TODAY


def said(date):
    return academics._pretty_due(date)


# ------------------------------------------------------------- the near days
check("today is 'today'", said("2026-08-11") == "today", said("2026-08-11"))
check("tomorrow is 'tomorrow'", said("2026-08-12") == "tomorrow",
      said("2026-08-12"))
check("yesterday is 'yesterday'", said("2026-08-10") == "yesterday",
      said("2026-08-10"))
check("a day later this week is named by its weekday",
      said("2026-08-14") == "Friday", said("2026-08-14"))
check("a few days back is counted",
      said("2026-08-08") == "3 days ago", said("2026-08-08"))

# ------------------------------------------------- where counting has to stop
# The bug: every past date, however old, came back as a day count.
check("a lecture from last year is named, not counted",
      said("2025-11-14") == "14 November 2025", said("2025-11-14"))
check("a date a month back is named, not counted",
      said("2026-07-02") == "2 July", said("2026-07-02"))
check("a week back is already too far to count",
      "days ago" not in said("2026-08-04"), said("2026-08-04"))
check("six days back is still counted",
      said("2026-08-05") == "6 days ago", said("2026-08-05"))

# ------------------------------------------------------------------ the year
# A bare "14 November" is a different day depending on the year it is read in.
check("a date in another year says which year",
      said("2027-03-09") == "9 March 2027", said("2027-03-09"))
check("a date in THIS year does not repeat the year",
      said("2026-12-25") == "25 December", said("2026-12-25"))

# ------------------------------------------------------------- the bad inputs
check("an empty date says nothing", said("") == "", repr(said("")))
check("a nonsense date says nothing", said("not-a-date") == "",
      repr(said("not-a-date")))
# _MONTHS[m - 1] with m == 0 is December, not an IndexError: a negative index is
# the LAST element. The same trap that once labelled every untied assignment
# with whichever class happened to be last.
check("month 0 is not silently read as December",
      "December" not in said("2026-00-14"), said("2026-00-14"))
check("month 13 is not accepted", "January" not in said("2026-13-14"),
      said("2026-13-14"))

# --------------------------------------------------------- the other languages
# nbi18n reads NB_LANG at import, so each language needs its own process. The
# expectation is not hardcoded: it is whatever nbi18n itself produces when the
# WHOLE date string is handed to _t(), which is the contract being tested —
# _pretty_due must go through that path rather than around it.
CHILD = r'''
import os, sys
sys.path.insert(0, %r)
import nbi18n, academics
academics._today_key = lambda: "2026-08-11"
print("%%s\t%%s" %% (academics._pretty_due("2026-09-14"),
                   nbi18n._t("14 September")))
''' % (DE,)

for lang in ("zh", "ja", "ko", "es", "fr", "ru"):
    home = "/tmp/nbhome-acaddates-%s-%d" % (lang, os.getpid())
    os.makedirs(home + "/.config/notebook", exist_ok=True)
    env = dict(os.environ, NB_LANG=lang, NB_HOME=home)
    r = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        check("%s renders a date at all" % lang, False, r.stderr.strip()[-200:])
        continue
    got, want = r.stdout.strip().split("\t")
    check("%s writes the date the way %s writes dates" % (lang, lang),
          got == want,
          "%r, expected the form nbi18n produces for a whole date (%r)"
          % (got, want))
    # A catalog that simply has no translation is not evidence of anything, so
    # say so rather than counting it as a pass.
    if want == "14 September":
        print("     (note: %s catalog left this date in English)" % lang)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("RESULT: %s" % ("ALL PASS" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
