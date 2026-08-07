#!/usr/bin/env python3
"""Open a DAMAGED store, make a REAL edit, close — three times over — and prove
the user's own bytes are still on disk at the end.

WHY THIS EXISTS ALONGSIDE THE TWO SUITES THAT LOOK LIKE IT:

  * tools/store_damage_selftest.py drives every realistic damage SHAPE, but
    only ONE open+close per shape. The loss found on 2026-07-29 needed two.
  * tools/reopen_damage_selftest.py drives THREE open+close cycles, but only
    one shape (valid JSON that no app recognises) and no user action at all.

Neither covers the combination, and the combination is where the money is: the
first close writes the app's blank state over the store (survivable, because
nbapp.preserve_damaged has just copied the real bytes to <store>.bak) and the
SECOND close is what overwrites that backup. A shape that only half-fails --
where the app reads three records out of four -- reaches that path too, and no
suite drove one twice.

It also covers the two stores missing from store_damage_selftest entirely:
ebook.json (a shelf plus every book's reading position) and terminal.json.

EVERY CYCLE MAKES A REAL EDIT through the real handler, because a store is only
destroyed by a SAVE: an app that never writes cannot lose anything, and a suite
that only opens and closes it is measuring nothing.

ONE OPEN PER PROCESS, always: nbapp._BACKED_UP is module state ("the version
from before this session touched it"), so driving the cycles inside a single
process is exactly the lie this family of suites exists to catch. The driver
re-invokes itself per cycle.

  DISPLAY=:0 python3 tools/reopen_shapes_selftest.py [app ...]
"""
import os
import sys
import json
import time
import shutil
import zipfile
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
CYCLES = 3
MARK = "KEEPME-9Q7"

# ------------------------------------------------------------- healthy stores
# Every one of these carries MARK inside the user's own words, so "did this
# survive?" is a byte search rather than a shape the app might have rewritten.
GOOD = {
    "cookbook": {
        "cats": ["Dinner", "Baking"], "active_cat": 0, "sel": 0,
        "recipes": [
            {"title": "%s stew" % MARK, "cat": "Dinner", "desc": "Sunday",
             "time": "3h", "makes": "Serves 6", "effort": "Easy",
             "ing": "500g beef\n2 carrots", "steps": "Brown it.\nWait.",
             "photo": ""},
            {"title": "%s bread" % MARK, "cat": "Baking", "desc": "",
             "time": "1h", "makes": "1 loaf", "effort": "Easy",
             "ing": "flour", "steps": "Bake it.", "photo": ""},
            {"title": "%s tart" % MARK, "cat": "Baking", "desc": "",
             "time": "2h", "makes": "Serves 8", "effort": "Hard",
             "ing": "lemon", "steps": "Chill it.", "photo": ""}]},

    "mealplanner": {"plan": {
        "2026-07-27": {"breakfast": {"kind": "note", "title": "%s porridge" % MARK},
                       "dinner": {"kind": "recipe", "title": "%s stew" % MARK}},
        "2026-07-28": {"lunch": {"kind": "takeout", "title": "%s chip shop" % MARK}},
        "2026-07-29": {"dinner": {"kind": "note", "title": "%s at Mum's" % MARK}}}},

    "language": {
        "xp": 250, "streak": 7, "streak_day": "2026-07-28",
        "crowns": {"eo:0:0": 3, "eo:0:1": 1, "eo:1:0": 2},
        "seen": ["eo:%s-one" % MARK, "eo:%s-two" % MARK, "eo:%s-three" % MARK]},

    "ebook": {"books": [
        {"path": "/root/Documents/%s-one.epub" % MARK, "title": "%s Novel" % MARK,
         "fmt": "EPUB", "pos": 12, "frac": 0.61, "total": 40,
         "author": "%s A" % MARK},
        {"path": "/root/Documents/%s-two.pdf" % MARK, "title": "%s Manual" % MARK,
         "fmt": "PDF", "pos": 3, "frac": 0.2, "total": 9, "author": ""},
        {"path": "/root/Documents/%s-three.epub" % MARK, "title": "%s Diary" % MARK,
         "fmt": "EPUB", "pos": 0, "frac": 0.0, "total": 5, "author": ""}],
        "open": "/root/Documents/%s-one.epub" % MARK},

    # Two view preferences and a marker key a hand-edit might have left behind:
    # terminal.json is small, which is exactly the kind a half-finished write
    # leaves in a foreign shape.
    "terminal": {"font_scale": 1.3, "cursor_blink": False,
                 "note": "%s do not lose" % MARK},
}

STORE_NAME = {"cookbook": "cookbook.json", "mealplanner": "mealplanner.json",
              "language": "language.json", "ebook": "ebook.json",
              "terminal": "terminal.json"}


# ------------------------------------------------------------------ mutations
# Each returns the bytes to write. A realistic drift of ONE part of the store;
# the user's marked words are always still IN there, which is the whole point.
def _clone(app):
    return json.loads(json.dumps(GOOD[app]))


def m_control(app):
    return json.dumps(_clone(app))


def m_not_json(app):
    return "%s -- this file is not JSON at all" % MARK


def _records(app, d):
    """(container, key) of the app's list of records."""
    return {"cookbook": (d, "recipes"), "ebook": (d, "books")}[app]


def m_list_as_object(app):
    d = _clone(app)
    holder, key = _records(app, d)
    holder[key] = {"a": holder[key][0], "b": holder[key][1], "c": holder[key][2]}
    return json.dumps(d)


def m_record_is_string(app):
    d = _clone(app)
    holder, key = _records(app, d)
    holder[key][1] = "%s the second one, as a sentence" % MARK
    return json.dumps(d)


def m_wrapper_renamed(app):
    d = _clone(app)
    src = {"cookbook": "recipes", "mealplanner": "plan",
           "ebook": "books"}[app]
    d[{"cookbook": "library", "mealplanner": "week",
       "ebook": "shelf"}[app]] = d.pop(src)
    return json.dumps(d)


def m_bare_list(app):
    d = _clone(app)
    src = {"cookbook": "recipes", "ebook": "books"}[app]
    return json.dumps(d[src])


def m_bare_map(app):
    d = _clone(app)
    return json.dumps(d[{"mealplanner": "plan"}[app]])


def m_one_record_junk(app):
    d = _clone(app)
    if app == "mealplanner":
        d["plan"]["2026-07-28"] = "%s chip shop, lunch" % MARK
    elif app == "language":
        d["crowns"] = "%s three skills" % MARK
    return json.dumps(d)


def m_counter_is_string(app):
    d = _clone(app)
    d["xp"] = "250"
    d["streak"] = ["7"]
    return json.dumps(d)


def m_seen_is_object(app):
    d = _clone(app)
    d["seen"] = {k: 1 for k in d["seen"]}
    return json.dumps(d)


def m_scalar_where_map(app):
    """The records slot holds a SCALAR while the records themselves are still in
    the file under a sibling key -- a repair or a half-finished migration gone
    wrong, which is the case cookbook's _not_a_cookbook() was written for.

    Writing the scalar and DELETING the records would test nothing: the marked
    bytes would already be gone before the app ever opened, so the survival
    check would fail on a store this suite itself emptied. That is the first
    thing this mutation did, and it read as two release blockers."""
    d = _clone(app)
    key = {"terminal": "font_scale", "ebook": "books",
           "cookbook": "recipes", "mealplanner": "plan"}[app]
    if key in d:
        d["old_" + key] = d[key]
    d[key] = 7
    return json.dumps(d)


CASES = {
    "cookbook": [("control", m_control), ("recipes is an object", m_list_as_object),
                 ("a recipe is a string", m_record_is_string),
                 ("wrapper key renamed", m_wrapper_renamed),
                 ("top level is a bare list", m_bare_list),
                 ("recipes is a number", m_scalar_where_map),
                 ("file is not json", m_not_json)],
    "mealplanner": [("control", m_control),
                    ("wrapper key renamed", m_wrapper_renamed),
                    ("top level is the bare week", m_bare_map),
                    ("one day is a string", m_one_record_junk),
                    ("plan is a number", m_scalar_where_map),
                    ("file is not json", m_not_json)],
    "language": [("control", m_control),
                 ("crowns is a string", m_one_record_junk),
                 ("xp/streak are strings", m_counter_is_string),
                 ("seen is an object", m_seen_is_object),
                 ("file is not json", m_not_json)],
    "ebook": [("control", m_control), ("books is an object", m_list_as_object),
              ("a book is a string", m_record_is_string),
              ("wrapper key renamed", m_wrapper_renamed),
              ("top level is a bare list", m_bare_list),
              ("books is a number", m_scalar_where_map),
              ("file is not json", m_not_json)],
    "terminal": [("control", m_control),
                 ("font_scale is not a number", m_scalar_where_map),
                 ("file is not json", m_not_json)],
}


# --------------------------------------------------------------------- worker
# Runs in its OWN process, per cycle. _APP_DIR is repointed first: nbapp's
# claim_single_instance() calls os._exit(0) on finding a live registration in
# the shared /tmp/nb-apps, which would end this worker with no output and status
# 0 -- a silent false pass.
WORKER = r'''
import os, sys, time, json, zipfile
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import nbapp
HOME = os.environ["NB_HOME"]
nbapp._APP_DIR = os.path.join(HOME, "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
app, cycle = sys.argv[1], int(sys.argv[2])


def pump(secs=0.0):
    end = time.time() + secs
    while True:
        for _ in range(8):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        if time.time() >= end:
            return
        time.sleep(0.02)


def toplevel_dialog():
    for t in Gtk.Window.list_toplevels():
        if isinstance(t, Gtk.Dialog) and t.get_visible():
            return t
    return None


def entries(w):
    out = []
    stack = [w]
    while stack:
        x = stack.pop()
        if isinstance(x, Gtk.Entry):
            out.append(x)
        if isinstance(x, Gtk.Container):
            stack.extend(x.get_children())
    return out


if app == "cookbook":
    import cookbook
    w = cookbook.Cookbook()
    w.show_all(); w.get_child().show_all(); pump()
    read = len(w.recipes)
    # A real edit through the real handler: add a recipe and type a title.
    w.new_recipe(); pump()
    w.title_entry.set_text("Cycle %d dish" % cycle)
    pump(0.2)

elif app == "mealplanner":
    import mealplanner
    w = mealplanner.MealPlanner()
    w.show_all(); w.get_child().show_all(); pump()
    read = sum(len(v) for v in w.plan.values())
    day = mealplanner._date_key(w.week + (cycle % 7))

    def fill():
        d = toplevel_dialog()
        if d is None:
            return True
        es = entries(d)
        if es:
            es[0].set_text("Cycle %d supper" % cycle)
        d.response(Gtk.ResponseType.OK)
        return False

    GLib.timeout_add(60, fill)
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    ev.button = 1
    w._cells[(day, "dinner")].emit("button-press-event", ev)
    pump(0.3)

elif app == "language":
    import language
    w = language.Language()
    w.show_all(); w.get_child().show_all(); pump()
    read = len(w.progress.get("crowns", {}))
    # The destroy handler saves unconditionally, which IS this app's
    # destructive path -- no user action needed to reach it. Earn some XP
    # through the real counter anyway so each cycle differs.
    # _bump_streak_xp was renamed to _award_xp; the old name silently made
    # every language case unrunnable, which this harness then counted as LOST.
    w._award_xp(5)
    pump(0.1)

elif app == "ebook":
    import ebook
    w = ebook.EbookReader()
    w.show_all(); w.get_child().show_all(); pump()
    read = len(w._books)
    p = os.path.join(HOME, "cycle%d.epub" % cycle)
    z = zipfile.ZipFile(p, "w")
    z.writestr("mimetype", "application/epub+zip")
    z.writestr("META-INF/container.xml",
               '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:'
               'opendocument:xmlns:container" version="1.0"><rootfiles>'
               '<rootfile full-path="b.opf" media-type="application/oebps-'
               'package+xml"/></rootfiles></container>')
    z.writestr("c.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                          '<h1>C</h1><p>Cycle text.</p></body></html>')
    z.writestr("b.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.'
               'org/2007/opf" version="2.0" unique-identifier="i"><metadata '
               'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Cycle'
               '</dc:title></metadata><manifest><item id="c" href="c.xhtml" '
               'media-type="application/xhtml+xml"/></manifest><spine>'
               '<itemref idref="c"/></spine></package>')
    z.close()
    # The real Library-open handler, which adds to the shelf and saves.
    w._open_book(p)
    pump(0.3)

elif app == "terminal":
    import terminal
    w = terminal.Terminal()
    w.show_all(); w.get_child().show_all(); pump(1.0)
    read = -1 if not terminal.VTE_OK else 0
    # Zoom is the real handler that writes this store.
    w._zoom(1.1)
    pump(0.2)

else:
    print("NOAPP")
    raise SystemExit(2)

w.destroy()
pump(0.3)
print("READ %s" % read)
'''


def survivors(home):
    hits = []
    for root, _dirs, files in os.walk(home):
        for f in files:
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    if MARK.encode() in fh.read():
                        hits.append(os.path.relpath(p, home))
            except OSError:
                pass
    return sorted(hits)


def run_case(app, name, mutate, root):
    home = os.path.join(root, "%s-%s" % (app, abs(hash(name)) % 100000))
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg)
    with open(os.path.join(cfg, STORE_NAME[app]), "w", encoding="utf-8") as fh:
        fh.write(mutate(app))
    first = None
    for n in range(1, CYCLES + 1):
        env = dict(os.environ, NB_HOME=home,
                   DISPLAY=os.environ.get("DISPLAY", ":0"),
                   PYTHONPATH=DE + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c", WORKER, app, str(n)],
                           capture_output=True, text=True, timeout=300, env=env)
        if r.returncode != 0 or "READ" not in (r.stdout or ""):
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            return False, "cycle %d did not run: %s" % (
                n, tail[-1][:100] if tail else "no output")
        left = survivors(home)
        if first is None:
            first = left
        if not left:
            return False, ("DESTROYED by cycle %d (after cycle 1 it was in %s)"
                           % (n, ";".join(first) or "nowhere"))
    return True, ";".join(first)


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")] or list(CASES)
    root = tempfile.mkdtemp(prefix="reopen_shapes_")
    bad = []
    total = 0
    try:
        for app in want:
            if app not in CASES:
                print("SKIP %s (no cases)" % app)
                continue
            for name, mutate in CASES[app]:
                total += 1
                ok, detail = run_case(app, name, mutate, root)
                print("%s %-12s %-28s %s"
                      % ("PASS" if ok else "FAIL", app, name, detail))
                if not ok:
                    bad.append("%s/%s" % (app, name))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("")
    print("%d shapes x %d open+edit+close cycles, %d survived, %d FAILED"
          % (total, CYCLES, total - len(bad), len(bad)))
    if bad:
        print("RESULT: SOME FAILED  (" + ", ".join(bad) + ")")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
