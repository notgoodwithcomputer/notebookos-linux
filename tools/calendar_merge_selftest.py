#!/usr/bin/env python3
"""A second writer must not cost anybody an event.

    tools/guestrun.sh python3 tools/calendar_merge_selftest.py

calendar.json is not this app's private store — tasks.py writes schedule entries
into the same file. So every save is a merge, and `_save_events` re-reads the
file first to fold back anything that appeared while Calendar was open. That
machinery makes four promises in its docstrings, all of them about not losing
somebody's event, and **no suite named `_merge_disk_events`, `_read_events_file`
or `_norm_event`**. They came off the day-5 method-coverage map: 94 of
calendar.py's 135 functions are never named by any suite.

THE DEFECT THIS FOUND. `_read_events_file` ended with

    return [it for it in items if isinstance(it, dict)]

and that one clause quietly broke the promise made directly below it — that an
unsalvageable record is "carried through the write untouched rather than
dropped". A row that is not a dict never reached the orphan path at all, so the
next wholesale rewrite dropped it in silence. Measured, four rows planted:

    {"title": "Good", "date": ...}   a real event        kept
    "this is not an event"           a bare string       *** DROPPED ***
    {"no": "date"}                   malformed dict      kept
    {"title": "No date either"}      malformed dict      kept

The filter was also redundant: `_norm_event` opens with its own
`isinstance(item, dict)` guard and returns None for anything else, which is
precisely the signal the orphan path waits for. Removing it restores the promise
and changes nothing else — the only caller is `_merge_disk_events`.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CALENDAR_MODULE_DIR:

  1. the dict filter is restored — the defect, put back
     (`return self._event_list(data)` -> the filtered comprehension)
                                                                  1 FAILED
       FAIL a record we cannot read at all is kept, whatever shape it is
            <- the bare string is gone; 3 rows where 4 went in

  2. the merge is skipped entirely
     (`self._merge_disk_events()` -> `pass`)                       2 FAILED
       FAIL an event another writer appended survives our save
       FAIL ...and so does a malformed row beside it

  3. a deleted event is resurrected from the stale file
     (the `rt & self._seen` test dropped)                          1 FAILED
       FAIL an event this session deleted stays deleted

  4. memory stops winning, so the same event lands twice
     (`if rt & mem_tokens: continue` dropped)                      1 FAILED
       FAIL an event added here and added again by another writer appears once
            <- 'Standup' on disk twice

     THIS PROOF CHANGED THE SUITE. It first came back CLEAN against the "edit
     in memory beats the stale copy" check, and the reason is worth keeping:
     after an edit the ORIGINAL tokens are still in `_seen`, so the stale disk
     copy is skipped as a deliberate DELETION and `mem_tokens` is never
     consulted at all. That check tests `_seen`, not the thing it is named
     after. Reaching `mem_tokens` needs an event added THIS session — never
     read from disk, so not in `_seen` — that another writer adds
     independently. Both checks are kept; they cover different guards.
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

H = "/tmp/nbhome-calmergesuite-%d" % os.getpid()
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


def ev(title, day="2026-08-20", start=9.0):
    return {"title": title, "date": day, "start": start, "end": start + 1,
            "cal": "Personal", "all_day": False}


def rows_on_disk():
    """Every record in the file, whatever shape — a filter here would hide
    exactly the bug this suite exists for."""
    try:
        with open(STORE) as fh:
            data = json.load(fh)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("events", [])
    return data if isinstance(data, list) else []


def titles():
    out = []
    for r in rows_on_disk():
        out.append(r.get("title", "<no title>") if isinstance(r, dict)
                   else "<raw:%s>" % (r,))
    return sorted(out)


def open_on(seed):
    with open(STORE, "w") as fh:
        json.dump(seed, fh)
    app = cal.Calendar()
    pump()
    return app


def second_writer(add):
    """What tasks.py (or a hand edit) does to the file behind our back."""
    rows = rows_on_disk()
    rows.extend(add)
    with open(STORE, "w") as fh:
        json.dump(rows, fh)


# ------------------------------------------- a foreign write is folded back in
app = open_on([ev("Mine")])
second_writer([ev("From Tasks", "2026-08-21")])
app._save_events()
pump()
check("an event another writer appended survives our save",
      "From Tasks" in titles(), titles())
check("...and ours is still there too", "Mine" in titles(), titles())
app.destroy()
pump()

# ------------------------------------------- an unreadable row is not dropped
# The defect: only DICTS were carried through. A bare string was filtered out
# before the orphan path ever saw it.
app = open_on([ev("Good")])
second_writer(["this is not an event", {"no": "date"},
               {"title": "No date either"}])
before = len(rows_on_disk())
app._save_events()
pump()
after = rows_on_disk()
check("a record we cannot read at all is kept, whatever shape it is",
      len(after) == before, "%d rows went in, %d came out: %s"
      % (before, len(after), titles()))
check("...including one that is not even a dict",
      any(not isinstance(r, dict) for r in after), titles())
check("...and a malformed dict beside it",
      any(isinstance(r, dict) and "date" not in r for r in after), titles())
app.destroy()
pump()

# ------------------------------------------------- a deletion is not undone
app = open_on([ev("Keep"), ev("Delete me", "2026-08-22")])
app.events = [e for e in app.events if e.get("title") != "Delete me"]
app._save_events()
pump()
app._save_events()          # a second write, now against our own rewritten file
pump()
check("an event this session deleted stays deleted",
      "Delete me" not in titles(), titles())
check("...and the one beside it is untouched", "Keep" in titles(), titles())
app.destroy()
pump()

# ------------------------------------------------- memory beats a stale disk copy
app = open_on([ev("Original")])
for e in app.events:
    e["title"] = "Edited"
app._save_events()
pump()
check("an edit in memory beats the stale copy on disk",
      "Edited" in titles() and "Original" not in titles(), titles())
app.destroy()
pump()

# ------------------------- the same event from both sides is not duplicated
# This is what actually exercises the memory-wins test. The "edit" case above
# does NOT: after an edit the ORIGINAL tokens are still in `_seen`, so the stale
# disk copy is skipped as a deliberate deletion and `mem_tokens` is never
# consulted. Reaching it needs an event added THIS session (so it was never read
# from disk, so it is not in `_seen`) that another writer adds independently.
# Measured: with the memory test disabled, "Standup" lands on disk TWICE.
with open(STORE, "w") as fh:
    json.dump([], fh)
app = cal.Calendar()
pump()
app.events.append(app._norm_event(ev("Standup", "2026-08-25")))
second_writer([ev("Standup", "2026-08-25")])
app._save_events()
pump()
check("an event added here and added again by another writer appears once",
      titles().count("Standup") == 1, titles())
app.destroy()
pump()

# ------------------------------------------------------------- never raises
HOSTILE = ['{"events": "not a list"}', "[]", '"a string"', "null",
           '{"events": [null, 1, [], {}]}', "{}", "not json at all", "",
           '[1, 2, 3]', '[[]]', '[{"date": "not-a-date"}]']
raised = []
for raw in HOSTILE:
    try:
        with open(STORE, "w") as fh:
            fh.write(raw)
        a2 = cal.Calendar()
        pump()
        a2._save_events()
        pump()
        a2.destroy()
        pump()
    except Exception as exc:                                  # noqa: BLE001
        raised.append((raw[:22], type(exc).__name__, str(exc)[:40]))
check("no shape of stored file makes a save raise (%d tried)" % len(HOSTILE),
      not raised, raised[:3])

shutil.rmtree(H, ignore_errors=True)
bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
