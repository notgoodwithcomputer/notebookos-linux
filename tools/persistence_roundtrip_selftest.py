#!/usr/bin/env python3
"""
Headless PERSISTENCE ROUND-TRIP selftest for the JSON-model apps.

For each app that keeps a JSON model on disk we prove the full save/reload
contract end-to-end:

  1. construct the app window against a fresh, empty temp NB_HOME,
  2. add ONE uniquely-identifiable item through the app's own real add path,
  3. trigger the app's real save,
  4. construct a SECOND, independent instance (which re-reads the file), and
  5. assert the added item is present in the reloaded in-memory model.

This catches the whole class of "it saved but didn't come back" (or the
reverse) bugs that a single-instance test can't see, because it exercises the
real serialize -> file -> parse -> model pipeline across a process-like restart.

Apps covered: accounting, tasks, cookbook, calendar, novel, contacts, journal.
If an app's add/save API can't be located, that app is SKIPped (printed, but it
does NOT fail the run) rather than reported as a failure.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/tmp/rt python3 persistence_roundtrip_selftest.py

NB_HOME in the environment is ignored — each app gets its own throwaway home so
the test never touches the caller's real data.
"""
import inspect
import os
import shutil
import sys
import tempfile
import traceback

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

_SHARED = ("nbapp", "nbicons", "widgets", "splash")

results = []   # bools: True=PASS, False=FAIL
skipped = []   # app names that were skipped (API not determinable)


class Skip(Exception):
    """Raised by a driver when the app's add/save API can't be located."""


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    results.append(bool(ok))


def pump():
    for _ in range(4):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def find_window_cls(mod):
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    return None


def import_fresh(app, home):
    """Point NB_HOME at `home` and import `app` fresh so its module-level
    config paths bind to that home; return its Gtk.Window subclass."""
    os.environ["NB_HOME"] = home
    for m in (app,) + _SHARED:
        sys.modules.pop(m, None)
    mod = __import__(app)
    cls = find_window_cls(mod)
    if cls is None:
        raise Skip("no Gtk.Window subclass in module")
    return cls


def require(win, *names):
    """Skip the app unless every named attribute/method is present."""
    for n in names:
        if not hasattr(win, n):
            raise Skip("missing " + n)


# --------------------------------------------------------------------------
# Per-app drivers.  Each: build instance #1, add one marked item via the real
# API, save via the real API, build instance #2, and return whether the marked
# item survived into the reloaded model.  Raise Skip if the API isn't there.
# --------------------------------------------------------------------------
def rt_accounting(cls):
    marker = "RT-ACCT-UNIQUE-7F3"
    w1 = cls(); pump()
    require(w1, "add_entry", "tx")
    n0 = len(w1.tx)
    w1.add_entry(marker, -12.34)     # appends + autosaves to accounting.json
    pump()
    w2 = cls(); pump()               # reloads from disk
    present = (len(w2.tx) == n0 + 1
               and any(t.get("desc") == marker for t in w2.tx))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_tasks(cls):
    marker = "RT TASK UNIQUE Q9"     # plain words: no quick-add #/@ tokens
    w1 = cls(); pump()
    require(w1, "_on_add", "tasks", "_save_tasks")
    n0 = len(w1.tasks)
    ent = Gtk.Entry(); ent.set_text(marker)
    w1._on_add(ent)                  # real quick-add path: appends + _save_tasks
    pump()
    w2 = cls(); pump()
    present = (len(w2.tasks) == n0 + 1
               and any(t.get("title") == marker for t in w2.tasks))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_cookbook(cls):
    marker = "RT-COOKBOOK-UNIQUE-Kx"
    w1 = cls(); pump()
    require(w1, "new_recipe", "recipes", "_save_state")
    n0 = len(w1.recipes)
    w1.new_recipe()                  # real add: appends a blank recipe, selects it
    w1.recipes[w1.sel]["title"] = marker   # user-typed title on the new recipe
    w1._save_state()                 # real autosave writer
    pump()
    w2 = cls(); pump()
    present = (len(w2.recipes) == n0 + 1
               and any(r.get("title") == marker for r in w2.recipes))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_calendar(cls):
    marker = "RT-CAL-UNIQUE-Vp"
    w1 = cls(); pump()
    require(w1, "events", "_save_events", "sel")
    n0 = len(w1.events)
    # The add-event path lives inside a modal dialog (dlg.run()), which can't be
    # driven headlessly; append to the real model and persist through the real
    # save method — the same serialize/parse pipeline a dialog add would use.
    w1.events.append({"date": w1.sel, "start": 9.0, "end": 10.0,
                      "title": marker, "cal": "Personal"})
    w1._save_events()
    pump()
    w2 = cls(); pump()
    present = (len(w2.events) == n0 + 1
               and any(e.get("title") == marker for e in w2.events))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_novel(cls):
    marker = "RT-NOVEL-UNIQUE-BODY-Zt"
    w1 = cls(); pump()
    require(w1, "_on_new_chapter", "chapters", "_save_state", "_buffer_text")
    n0 = len(w1.chapters)
    w1._on_new_chapter()             # real add: appends a chapter, selects it
    w1.chapters[-1]["buffer"].set_text(marker)   # user-typed body text
    w1._save_state()
    pump()
    w2 = cls(); pump()
    present = (len(w2.chapters) == n0 + 1
               and any(w2._buffer_text(c["buffer"]) == marker
                       for c in w2.chapters))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_contacts(cls):
    marker = "RT-CONTACT-UNIQUE-Nm"
    w1 = cls(); pump()
    require(w1, "_new_contact", "people", "_commit_edits")
    n0 = len(w1.people)
    w1._new_contact()                # real add: appends a card, enters edit mode
    if "name" not in getattr(w1, "_entries", {}):
        raise Skip("edit entries not built")
    w1._entries["name"].set_text(marker)   # edit the name field for real
    w1._commit_edits()               # real commit: writes back + _save()
    pump()
    w2 = cls(); pump()
    present = (len(w2.people) == n0 + 1
               and any(p.get("name") == marker for p in w2.people))
    w1.destroy(); w2.destroy(); pump()
    return present


def rt_journal(cls):
    marker = "RT-JOURNAL-UNIQUE-body-Jq"
    w1 = cls(); pump()
    require(w1, "new_entry", "entries", "_save_current", "_persist", "body")
    n0 = len(w1.entries)
    w1.new_entry()                   # real add: inserts a fresh entry, persists
    w1.body.get_buffer().set_text(marker)   # user-typed body text
    w1._save_current()               # copy buffer -> entry model
    w1._persist()                    # real writer
    pump()
    w2 = cls(); pump()
    present = (len(w2.entries) == n0 + 1
               and any(e.get("text") == marker for e in w2.entries))
    w1.destroy(); w2.destroy(); pump()
    return present


JOBS = [
    ("accounting", rt_accounting),
    ("tasks", rt_tasks),
    ("cookbook", rt_cookbook),
    ("calendar", rt_calendar),
    ("novel", rt_novel),
    ("contacts", rt_contacts),
    ("journal", rt_journal),
]


def main():
    saved_home = os.environ.get("NB_HOME")
    root = tempfile.mkdtemp(prefix="persist_roundtrip_")
    try:
        for app, driver in JOBS:
            home = os.path.join(root, app)
            os.makedirs(home, exist_ok=True)
            try:
                cls = import_fresh(app, home)
                present = driver(cls)
                check("%s roundtrip" % app, present)
            except Skip as s:
                print("SKIP %s (%s)" % (app, s))
                skipped.append(app)
            except Exception as e:
                check("%s roundtrip (%s)" % (app, type(e).__name__), False)
                for line in traceback.format_exc().rstrip().splitlines():
                    print("    | " + line)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if saved_home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = saved_home

    if skipped:
        print("SKIPPED: " + ", ".join(skipped))
    ok = all(results)
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
