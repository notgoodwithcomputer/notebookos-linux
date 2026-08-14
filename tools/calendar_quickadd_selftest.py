#!/usr/bin/env python3
"""What somebody types into the quick-add box becomes the event they meant.

    tools/guestrun.sh python3 tools/calendar_quickadd_selftest.py

`parse_quick_event` is the natural-language entry path — a time, a day and a
`#Calendar` mixed into a name, in any order. It is ~120 lines of parsing with a
docstring full of promises, and **no suite named it**. It came out of the
method-coverage map for day 5: calendar.py defines 135 functions and 94 are
never named by any of the nine existing suites, 48 of them domain logic. This
parser was top of that list after the recurrence engine.

MEASURED FIRST, AND IT IS CORRECT — every documented promise holds, and the
hostile battery below found nothing. So this suite is not a bug fix. It exists
because the behaviour is conservative BY CONSTRUCTION in ways that are very easy
to undo: what stops `at 25` becoming 01:00 is that the hour test rejects it and
the words fall through into the TITLE, and a refactor reaching for `% 24` would
look like a tidy-up.

THE PROMISE THAT IS EASIEST TO BREAK, and the reason the docstring argues for it
at length: a bare number is a TIME only after a word that announces one.

    "table for 4"        a table for four      -> title "table for 4", 09:00
    "table for 4 at 7"   ...at seven           -> title "table for 4", 19:00
    "Standup at 7"       a time                -> title "Standup",     19:00

Get that wrong and "table for 4" silently becomes a 16:00 appointment called
"table for".

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALENDAR_MODULE_DIR. MEASURED — the counts below are what ran, not what
I expected, and one of them changed the suite:

  1. a bare number is taken as a time without a word announcing one
     (`bare_ok=(prev in _TIME_LEAD)` -> `bare_ok=True`)              3 FAILED
       FAIL 'table for 4' -> title 'table for', 16:00
       FAIL 'table for 4 at 7'
       FAIL a number in a name is not a time

  2. a weekday resolves backward instead of forward
     (`base_day + timedelta(days=ahead)` -> `base_day - ...`)        3 FAILED
       FAIL a weekday means the one COMING
       FAIL ...but a trailing one is only the day
       FAIL THURSDAY is thursday

  3. an out-of-range 24-hour time is accepted
     (`elif hh > 23:` -> `elif False:`)                              1 FAILED
       FAIL 'Gym 25:00' is not a time, so it stays in the name

  3b. an out-of-range 12-hour time is accepted
     (`if not 1 <= hh <= 12:` -> `<= 24`)                            1 FAILED
       FAIL 'Gym 13pm' is not a time, so it stays in the name

  4. the empty-title guard stops returning None
     (`if not title: return None` -> `title = "Event"`)              5 FAILED
       FAIL ',' / '-' / ' , ' / '--' / ', ,' alone is not an event either
     THIS ONE CHANGED THE SUITE. It first came back CLEAN, because for '3pm'
     the words are all consumed, `keep` is empty, and `all([])` is TRUE — so the
     day-words guard returns None first and the empty-title guard is never
     reached. Only input that survives as PUNCTUATION gets there. The five
     checks above exist because a red proof failed to land, which is exactly
     what red proofs are for.

  5. a weekday opening the line is used but not kept
     (`if not keep: keep.append(w)` -> `if False:`)                   1 FAILED
       FAIL a leading weekday is used as the day AND kept in the name
     The scar the source comments record: this turned "Sunday lunch at noon"
     into an event called "lunch".
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALENDAR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="cal-quick-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

from datetime import date                                     # noqa: E402
import calendar as cal                                        # noqa: E402

# A Sunday, so "thursday" has somewhere forward to go and the weekday tests are
# not accidentally reading today.
BASE = date(2026, 8, 9)
CALS = ("Personal", "Work", "Family")
R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def parse(text):
    return cal.parse_quick_event(text, BASE, CALS)


def expect(text, title, day, hour, calendar_name=None):
    got = parse(text)
    want = (title, day, hour, calendar_name)
    check("%r -> %s %s %s" % (text, title, day, hour), got == want,
          "got %r" % (got,))


# ------------------------------------------------------------- times
for text, hour in (("Dentist 3pm", 15.0), ("Dentist 15:00", 15.0),
                   ("Dentist 9.30am", 9.5), ("Dentist 9:30", 9.5),
                   ("Lunch noon", 12.0), ("Lunch midday", 12.0),
                   ("Party midnight", 0.0), ("Standup at 7", 19.0),
                   ("Early 0:00", 0.0)):
    got = parse(text)
    check("%r is at %s" % (text, hour), got is not None and got[2] == hour,
          got)

# ------------------------------------------------------------- days
check("'today' is the base day", parse("Gym today")[1] == BASE,
      parse("Gym today"))
check("'tomorrow' is the day after",
      parse("Gym tomorrow")[1] == date(2026, 8, 10), parse("Gym tomorrow"))
# BASE is a Sunday; Thursday coming is the 13th, not the 6th just gone.
check("a weekday means the one COMING",
      parse("Gym thursday")[1] == date(2026, 8, 13), parse("Gym thursday"))
check("...and a bare date resolves forward too",
      parse("Bins 14/8")[1] == date(2026, 8, 14), parse("Bins 14/8"))
for text in ("Party 14 August", "Party August 14"):
    check("%r reads either way round" % text,
          parse(text)[1] == date(2026, 8, 14), parse(text))
check("an abbreviated month is understood",
      parse("Talk Sept 3")[1] == date(2026, 9, 3), parse("Talk Sept 3"))

# ------------------------------------------------------- the calendar tag
check("#Calendar files it", parse("Review #Work")[3] == "Work",
      parse("Review #Work"))
check("...case-insensitively", parse("Review #work")[3] == "Work",
      parse("Review #work"))
check("...by prefix", parse("Review #Fam")[3] == "Family",
      parse("Review #Fam"))
check("a tag that matches no calendar stays in the name",
      parse("Review #nope") == ("Review #nope", BASE, 9.0, None),
      parse("Review #nope"))

# ------------------------------------- a bare number is not a time (THE one)
expect("table for 4", "table for 4", BASE, 9.0)
expect("table for 4 at 7", "table for 4", BASE, 19.0)
expect("Standup at 7", "Standup", BASE, 19.0)
check("a number in a name is not a time",
      parse("Gym 5")[2] == 9.0, parse("Gym 5"))

# --------------------------------------------- nothing to file is not an event
for text in ("3pm", "noon", "midnight", "today", "thursday", "#Work",
             "", "   ", None):
    check("%r alone is not an event" % (text,), parse(text) is None,
          parse(text))

# Punctuation alone is not a name either, and this is the case that reaches the
# empty-title guard. For "3pm" the words are all consumed, `keep` is empty, and
# `all([])` is TRUE — so the day-words guard returns None first and the
# empty-title guard is never reached. Only input that survives as punctuation
# gets there. (Found by a red proof that failed to land: mutating the
# empty-title guard changed nothing until this case existed.)
for text in (",", "-", " , ", "--", ", ,"):
    check("%r alone is not an event either" % text, parse(text) is None,
          parse(text))

# ------------------------------ an impossible time or date is NAME, not a guess
# What keeps "at 25" from becoming 01:00 is that the hour test refuses it and
# the words fall through into the title. That is the conservative choice and it
# is one `% 24` away from being silently wrong.
for text in ("Gym at 25", "Gym 13pm", "Gym 25:00", "Gym 12:99", "Gym at -1"):
    got = parse(text)
    check("%r is not a time, so it stays in the name" % text,
          got is not None and got[2] == 9.0 and got[0] == text, got)
for text in ("Gym 29 February", "Gym 31 April", "Gym 32/13", "Gym 31/2"):
    got = parse(text)
    check("%r is not a date, so it stays in the name" % text,
          got is not None and got[1] == BASE and got[0] == text, got)

# -------------------------------- a weekday that OPENS the line stays in the name
# A recorded scar: "Sunday lunch", "Friday prayers", "Monday club" are names
# that happen to start with a day. Using the day AND dropping the word turned
# "Sunday lunch at noon" into an event called "lunch".
got = parse("Sunday lunch at noon")
check("a leading weekday is used as the day AND kept in the name",
      got == ("Sunday lunch", date(2026, 8, 9), 12.0, None), got)
check("...but a trailing one is only the day",
      parse("Gym thursday") == ("Gym", date(2026, 8, 13), 9.0, None),
      parse("Gym thursday"))

# --------------------------------------------------------------- case
check("THURSDAY is thursday", parse("Gym THURSDAY")[1] == date(2026, 8, 13),
      parse("Gym THURSDAY"))
check("NOON is noon", parse("Gym NOON")[2] == 12.0, parse("Gym NOON"))

# ------------------------------------------------- the first of each wins
check("a second time is left in the name",
      parse("Gym 3pm 5pm") == ("Gym 5pm", BASE, 15.0, None),
      parse("Gym 3pm 5pm"))
check("...and so is a second day",
      parse("Gym today tomorrow") == ("Gym tomorrow", BASE, 9.0, None),
      parse("Gym today tomorrow"))

# ------------------------------------------------------------ never raises
# The docstring promises it outright, and this is a parser reachable from a text
# box, so it is the promise most worth holding: anything a person can type or
# paste has to come back as an event or as None.
HOSTILE = [None, "", "   ", "\t\n", ":", "::", "/", "//", "#", "##", "#" * 50,
           "-", ".", "..", "at", "at at", "pm", "am", "3pm4pm", "1/2/3/4",
           "9999999999", "1e400", ":::00", "99999999999999999999/1",
           "\x00", "\x00pm", "a" * 10000, "#####Work",
           "\U0001F600 3pm", "‮3pm", "０３ｐｍ", "٣pm",
           ", , ,", "today today today", "at at at 7", "12:", "::12", "12::",
           "1//2", "/8", "14/", "Sept", "September", "Sept Sept 3",
           "0", "00", "000", "-1", "+1", "1.", ".5", "1.2.3"]
raised = []
for text in HOSTILE:
    try:
        parse(text)
    except Exception as exc:                                  # noqa: BLE001
        raised.append((repr(text)[:30], type(exc).__name__, str(exc)[:40]))
check("nothing a person can type makes it raise (%d inputs incl. NUL, a "
      "10k-char name, an RTL override, fullwidth and Arabic-Indic digits)"
      % len(HOSTILE), not raised, raised[:3])

# ------------------------------------------ the same words, in other languages
# Found by RENDERING the app in German and reading the quick-add hint, not by
# reading the parser. Day and month names go through _t(); three defects were
# hiding behind that:
#
#   fr  the catalog says "Aujourd\u2019hui" with a TYPOGRAPHIC apostrophe.
#       Every keyboard makes the ASCII one, so the French word for "today"
#       did not work at all.
#   tr  the catalog says "Yar\u0131n" with a DOTLESS i. str.lower() is not
#       locale-aware, so "YARIN".lower() is "yarin" and never matched: typing
#       in capitals lost the word.
#   all noon / midday / midnight were the ONLY day-time vocabulary still
#       hard-coded to English, so "Mittag" stayed in the name while
#       "Donnerstag" was understood — an inconsistency inside one sentence.
#
# These run in-process against the ACTIVE language, so they check English here
# and the machinery that serves the other sixteen.
check("_fold folds a typographic apostrophe to the ASCII one",
      cal._fold("Aujourd\u2019hui") == cal._fold("Aujourd'hui"),
      (cal._fold("Aujourd\u2019hui"), cal._fold("Aujourd'hui")))
check("...and a Turkish dotless i to a plain one, both ways of typing it",
      cal._fold("Yar\u0131n") == cal._fold("YARIN") == cal._fold("yarin"),
      (cal._fold("Yar\u0131n"), cal._fold("YARIN"), cal._fold("yarin")))
check("...and leaves an accent alone, because a missing tilde is a spelling "
      "mistake and not something the keyboard did",
      cal._fold("ma\u00f1ana") != cal._fold("manana"),
      (cal._fold("ma\u00f1ana"), cal._fold("manana")))
check("a folded word still strips the comma the old code stripped",
      cal._fold("Thursday,") == "thursday", cal._fold("Thursday,"))

# noon/midday/midnight now go through _t() like every other date word. Until the
# catalogs carry the keys _t() returns the English source, so these hold today
# and keep holding once the translations land.
for text, hour in (("Lunch noon", 12.0), ("Lunch midday", 12.0),
                   ("Party midnight", 0.0)):
    got = parse(text)
    check("%r still reads in English after localising it" % text,
          got is not None and got[2] == hour, got)

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
