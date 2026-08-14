#!/usr/bin/env python3
"""Opening a file that is not a calendar must not cost you your calendar.

    tools/guestrun.sh python3 tools/calendar_document_selftest.py

`_apply_document` promises to return False **"touching no state"** on an
unusable structure — explicitly so "a foreign JSON dict from the shared folder
(e.g. a ledger) that lacks a 'calendars' or 'events' key" cannot wipe the
calendar. Documents are shared: File ▸ Open reads whatever the person picks out
of Documents, next to every other app's JSON.

No suite named `_apply_document`, `_serialize_document`, `_load_document` or any
of the four `_file_*` entry points. They came off the day-5 method-coverage map.

TWO DEFECTS, both of which lost every event with the Open reported as success.

1. THE LIST WAS CHECKED, ITS CONTENTS WERE NOT. The guard asked only that
   `events` be a list. The load below keeps whatever `_norm_event` can salvage
   and assigns the result, so any foreign JSON carrying an `events` list emptied
   the calendar. Measured, opened over a calendar holding three events:

       {"events": ["track1", "track2"]}   a playlist   3 -> 0, returned True
       {"events": [1, 2, 3]}              a log        3 -> 0, returned True
       {"events": [null, null]}                        3 -> 0, returned True
       {"events": [[[]]]}                              3 -> 0, returned True

   Now a non-empty `events` list must contain at least one record `_norm_event`
   can actually read. An explicitly EMPTY list still loads and still clears —
   that one is the document saying so, and starting clean is a real thing to
   want.

2. SILENCE ABOUT EVENTS WAS TREATED AS "DELETE THEM". A dict with `calendars`
   and no `events` key at all was accepted, and because the load assigns
   `self.events` unconditionally, opening `{"calendars": [...]}` over three
   events took it to zero and swapped the calendar list. `_serialize_document`
   always writes both keys, so a dict without `events` was never one of ours.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALENDAR_MODULE_DIR:

  1. the contents check is removed — defect 1, put back
     (the `not any(self._norm_event(it) ...)` guard dropped)       4 FAILED
       FAIL a playlist does not replace the calendar
       FAIL a log file does not replace the calendar
       FAIL a list of nulls does not replace the calendar
       FAIL a nested list does not replace the calendar

  2. the events-key requirement is removed — defect 2, put back
     (`if "events" not in data: return False` dropped)             2 FAILED
       FAIL a document with calendars but no events leaves events alone
       FAIL ...and leaves the calendar list alone too

  3. an unrecognised document mutates before it rejects
     (`return False` on the missing-keys branch -> `self.events = []`)
                                                                   4 FAILED
       FAIL a ledger / a task list / a cookbook / an empty dict does not
            touch the calendar
     Four, not the three I predicted: the empty dict takes the same branch, and
     "touching no state" has to hold for it too.
"""
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALENDAR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

H = "/tmp/nbhome-caldocsuite-%d" % os.getpid()
os.environ["NB_HOME"] = H
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + "/.config/notebook")
STORE = os.path.join(H, ".config", "notebook", "calendar.json")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402
import uishot                                                 # noqa: E402
import nbapp                                                  # noqa: E402

uishot.load_theme()
nbapp.screen_size = lambda: (1024, 722)
import calendar as cal                                        # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def pump():
    for _ in range(8):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def ev(title, day="2026-08-20"):
    return {"title": title, "date": day, "start": 9.0, "end": 10.0,
            "cal": "Personal", "all_day": False}


SEED = [ev("Dentist"), ev("Standup", "2026-08-21"), ev("Bins", "2026-08-22")]


def opened_on_seed():
    with open(STORE, "w") as fh:
        json.dump(SEED, fh)
    app = cal.Calendar()
    pump()
    return app


def state(app):
    return (sorted(e.get("title", "?") for e in app.events),
            [c.get("name") for c in app.calendars])


# --------------------------------- a foreign file leaves everything alone
FOREIGN = [
    ("a ledger", {"tx": [{"amount": 5}], "opening": 0.0}),
    ("a task list", {"tasks": [{"text": "buy milk"}]}),
    ("a cookbook", {"recipes": []}),
    ("a bare string", "just text"),
    ("a number", 42),
    ("null", None),
    ("an empty dict", {}),
    ("an events value that is not a list", {"events": "nope"}),
    ("an events value that is a dict", {"events": {"a": 1}}),
]
for name, doc in FOREIGN:
    app = opened_on_seed()
    before = state(app)
    got = app._apply_document(doc)
    pump()
    check("%s does not touch the calendar" % name,
          got is False and state(app) == before,
          "returned %r, state %s" % (got, state(app)))
    app.destroy()
    pump()

# ------------------- an events LIST whose contents are not events (defect 1)
NOT_EVENTS = [
    ("a playlist", {"events": ["track1", "track2"]}),
    ("a log file", {"events": [1, 2, 3]}),
    ("a list of nulls", {"events": [None, None]}),
    ("a nested list", {"events": [[[]]]}),
]
for name, doc in NOT_EVENTS:
    app = opened_on_seed()
    before = state(app)
    got = app._apply_document(doc)
    pump()
    check("%s does not replace the calendar" % name,
          got is False and state(app) == before,
          "returned %r, state %s" % (got, state(app)))
    app.destroy()
    pump()

# --------------------------- silence about events is not a deletion (defect 2)
app = opened_on_seed()
before = state(app)
got = app._apply_document({"calendars": [{"name": "Work", "color": "#123456"}]})
pump()
check("a document with calendars but no events leaves events alone",
      state(app)[0] == before[0], "%s -> %s" % (before[0], state(app)[0]))
check("...and leaves the calendar list alone too",
      state(app)[1] == before[1], "%s -> %s" % (before[1], state(app)[1]))
app.destroy()
pump()

# ------------------------------------- what a real document must still do
app = opened_on_seed()
check("an explicitly EMPTY events list still clears the calendar",
      app._apply_document({"events": []}) is True and app.events == [],
      state(app))
app.destroy()
pump()

app = opened_on_seed()
got = app._apply_document({"events": [ev("Kept"), "junk", 7]})
pump()
check("a document with one readable event among rubbish loads that event",
      got is True and state(app)[0] == ["Kept"], (got, state(app)))
app.destroy()
pump()

# ----------------------------------------------------- a round trip survives
app = opened_on_seed()
before = state(app)
doc = app._serialize_document()
check("_serialize_document writes both keys, which is why one without "
      "'events' is not ours",
      set(doc) >= {"calendars", "events"}, sorted(doc))
app.events = []
app.calendars = []
got = app._apply_document(doc)
pump()
check("...and its own document loads back unchanged",
      got is True and state(app) == before, (got, state(app), before))
app.destroy()
pump()

shutil.rmtree(H, ignore_errors=True)
bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
