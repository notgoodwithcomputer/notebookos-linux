#!/usr/bin/env python3
"""Adversarial suite for the four writing apps — Writer, Novel, Screenplay,
Journal — driven through their REAL windows, headless.

Three things are proved here, in the order they matter on a machine with no
shell and no cloud:

  1. THE WINDOW BUILDS. Every one of these apps reads a session-recovery JSON
     file inside its constructor. If that read raises, the app is dead on every
     launch for good. Writer failed this on eight of nine damaged shapes before
     the fix that came with this file: `for s_off, e_off, name in doc["runs"]`
     raised ValueError on a runs field that was a string or a dict, a numeric
     body raised TypeError inside set_text, a "page" that was a string raised
     AttributeError inside _apply_page_geometry, and an image record with no
     "off" raised KeyError. See writer._sane_doc.

  2. THE USER'S BYTES SURVIVE THREE OPEN+CLOSE CYCLES. One open per process,
     always: nbapp._BACKED_UP is module state, so cycling inside one process is
     exactly the lie this measurement exists to catch. The store is damaged in
     ways that still PARSE as JSON — that is the case that reaches the "reads as
     no data" path, where the app opens blank and the close-time flush writes
     the blank over the only copy.

  3. THE DOCUMENT ROUND-TRIPS. Real edits through the real handlers, then a
     close, then a fresh process that reopens and diffs.

    DISPLAY=:0 python3 tools/writing_apps_selftest.py [app ...]

Nothing here writes outside a throwaway NB_HOME, and each worker repoints
nbapp._APP_DIR: claim_single_instance() calls os._exit(0) when it finds a live
registration in the shared /tmp/nb-apps, which would end a worker with NO output
and status 0 — a silent false pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))

MARK = "PROSE-MARKER-4K2"
CYCLES = 3

# ---------------------------------------------------------------------------
# The worker: builds the real window in a fresh process, optionally runs a
# scripted body against it, then closes it the way Esc does.
# ---------------------------------------------------------------------------
WORKER = r'''
import os, sys, json
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)

modname, clsname = sys.argv[1], sys.argv[2]
script = sys.argv[3] if len(sys.argv) > 3 else ""
mod = __import__(modname)


def pump():
    for _ in range(8):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


w = getattr(mod, clsname)()
# A Gtk.Stack will not switch to a child that has never been shown, and a
# widget marked no-show-all stays hidden until something shows it; realise the
# whole tree the way nbapp.run() does before asserting anything about it.
w.get_child().show_all()
pump()
out = {}
if script:
    exec(compile(script, "<script>", "exec"),
         {"w": w, "mod": mod, "Gtk": Gtk, "pump": pump, "out": out,
          "json": json, "os": os})
pump()
w.destroy()
pump()
print("RAN " + json.dumps(out))
'''


def run_worker(app, cls, home, script=""):
    """One open+close of `app` in a fresh process. Returns (ok, out, err)."""
    env = dict(os.environ, NB_HOME=home, DISPLAY=os.environ.get("DISPLAY", ":0"),
               PYTHONPATH=DE + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", WORKER, app, cls, script],
                       capture_output=True, text=True, timeout=240, env=env)
    line = ""
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("RAN "):
            line = ln[4:]
    if not line:
        err = [x.strip() for x in (r.stderr or r.stdout or "").splitlines()
               if x.strip() and not x.strip().startswith(("File \"", "Traceback"))]
        return False, {}, (err[-1][:150] if err else "no output, exit %d"
                           % r.returncode)
    try:
        return True, json.loads(line), ""
    except ValueError:
        return True, {}, ""


def survivors(home):
    """Every file under `home` still holding the user's bytes."""
    hits = []
    for root, _d, files in os.walk(home):
        for f in files:
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    if MARK.encode() in fh.read():
                        hits.append(os.path.relpath(p, home))
            except OSError:
                pass
    return sorted(hits)


# ---------------------------------------------------------------------------
# Damage shapes. All of these are VALID JSON: a file that does not parse is
# quarantined by nbapp.preserve_damaged and never overwritten, so the dangerous
# case is the one that parses perfectly and reads as "no data".
# ---------------------------------------------------------------------------
def damage_cases(app):
    body = "%s\nthe second line" % MARK
    if app == "writer":
        base = {"version": 2, "body": body}
        return [
            ("runs is an object", dict(base, runs={"bold": [0, 4]})),
            ("runs is a string", dict(base, runs="bold")),
            ("a run is a 2-list", dict(base, runs=[[0, 4]])),
            ("a run is a string", dict(base, runs=["bold"])),
            ("run tag is a number", dict(base, runs=[[0, 4, 7]])),
            ("run tag is unbuildable", dict(base, runs=[[0, 4, "size:huge"]])),
            ("run offsets past the end", dict(base, runs=[[0, 99999, "bold"]])),
            ("run offsets reversed", dict(base, runs=[[9, 2, "bold"]])),
            ("body is a number", {"version": 2, "body": 4711,
                                  "runs": [], "note": MARK}),
            ("page is a string", dict(base, runs=[], page="Letter")),
            ("margins are strings", dict(base, runs=[],
                                         page={"size": "A4",
                                               "margins": ["1", "1", "1", "1"]})),
            ("image record has no off", dict(base, runs=[],
                                             images=[{"path": "/nope.png"}])),
            ("tables is a string", dict(base, runs=[], tables="two")),
            ("table record is a string", dict(base, runs=[], tables=["x"])),
            ("header is a number", dict(base, runs=[], header=12)),
            ("no recognised keys", {"text": body}),
        ]
    if app == "novel":
        ch = {"num": "1", "title": "Chapter 1", "body": body}
        return [
            ("chapters is a string", {"title": "Book", "chapters": MARK}),
            ("chapters is an object", {"title": "Book",
                                       "chapters": {"one": ch}}),
            ("a chapter is a string", {"title": "Book", "chapters": [body]}),
            ("ranges is a list", {"title": "Book",
                                  "chapters": [dict(ch, ranges=[1, 2])]}),
            ("range spans are strings",
             {"title": "Book",
              "chapters": [dict(ch, ranges={"bold": ["a", "b"]})]}),
            ("parts are strings", {"title": "Book", "parts": ["one"],
                                   "chapters": [ch]}),
            ("active is a bool", {"title": "Book", "active": True,
                                  "chapters": [ch]}),
            ("num is an object",
             {"title": "Book", "chapters": [dict(ch, num={"n": 1})]}),
            ("title is a number", {"title": 99, "chapters": [ch]}),
            ("no recognised keys", {"text": body}),
        ]
    if app == "screenplay":
        base = {"title": "SCRIPT", "body": body}
        return [
            ("body_tags is an object", dict(base, body_tags={"a": 1})),
            ("a tag record is a string", dict(base, body_tags=["scene"])),
            ("tag offsets are strings",
             dict(base, body_tags=[{"tag": "scene", "start": "a", "end": "b"}])),
            ("body is a list", {"title": "SCRIPT", "body": [MARK],
                                "body_tags": []}),
            ("path is a number", dict(base, body_tags=[], path=5)),
            ("title is a number", dict(base, title=7, body_tags=[])),
            ("no recognised keys", {"text": body}),
        ]
    if app == "journal":
        en = {"day": "1", "wd": "Mon", "month_label": "June 2026",
              "date": "Monday, 1 June", "meta": "Written at 09:00",
              "title": MARK, "preview": "", "text": body}
        return [
            ("entries is an object", {"entries": {"a": en}, "active": 0}),
            ("an entry is a string", {"entries": [body], "active": 0}),
            ("tags is a string", {"entries": [dict(en, tags="bold")],
                                  "active": 0}),
            ("a tag span is a string",
             {"entries": [dict(en, tags=["bold"])], "active": 0}),
            ("tag offsets are strings",
             {"entries": [dict(en, tags=[{"tag": "bold", "start": "a",
                                          "end": "b"}])], "active": 0}),
            ("the file is a bare list", [en]),
            ("active is a bool", {"entries": [en], "active": True}),
            ("a field is null", {"entries": [dict(en, meta=None)], "active": 0}),
            ("day is a number", {"entries": [dict(en, day=3)], "active": 0}),
            ("no recognised keys", {"text": body}),
        ]
    return []


APPS = [("writer", "Writer", "writer.json"),
        ("novel", "Novel", "novel.json"),
        ("screenplay", "Screenplay", "screenplay.json"),
        ("journal", "Journal", "journal.json")]


# ---------------------------------------------------------------------------
# Round-trip scripts: real edits through the real handlers, in one process;
# then a second process reopens and reads the state back.
# ---------------------------------------------------------------------------
EDIT = {
    "writer": '''
buf = w.buf
buf.set_text("%(M)s heading\\nbody text here")
buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(7))
w._toggle_char("bold")
buf.place_cursor(buf.get_iter_at_offset(3))
w._set_style("Heading 1")
w._set_align("center")
# At the end of the document, not mid-word: _insert_table inserts at the CARET,
# which _set_style left inside the heading. Correct app behaviour, wrong test.
buf.place_cursor(buf.get_end_iter())
w._insert_table([["a", "b"], ["c", "d"]])
w._page = dict(w._page, size="A4")
w._header = "%(M)s hdr"
w._page_numbers = True
w._apply_page_geometry()
''' % {"M": MARK},
    "novel": '''
buf = w.view.get_buffer()
buf.set_text("Chapter 1\\n%(M)s prose")
buf.select_range(buf.get_iter_at_offset(10), buf.get_iter_at_offset(16))
w._on_fmt(None, "italic")
w._commit_manuscript_name("%(M)s Book")
w._on_new_chapter()
buf2 = w.view.get_buffer()
buf2.set_text("Chapter 2\\nsecond chapter %(M)s")
''' % {"M": MARK},
    "screenplay": '''
buf = w.body.get_buffer()
buf.set_text("INT. HOUSE - DAY\\n%(M)s action line")
w.scripttitle.set_text("%(M)s SCRIPT")
w.scriptsubtitle.set_text("by nobody")
buf.place_cursor(buf.get_iter_at_offset(20))
w._apply_element(0)
''' % {"M": MARK},
    "journal": '''
w.new_entry()
buf = w.body.get_buffer()
buf.set_text("%(M)s title line\\nthe body of the entry")
buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(5))
w._toggle_tag("bold")
w._save_current()
''' % {"M": MARK},
}

READBACK = {
    "writer": '''
d = w._serialize()
out["body"] = d["body"]
out["tags"] = sorted({r[2] for r in d["runs"]})
out["tables"] = len(d["tables"])
out["page"] = d["page"].get("size")
out["header"] = d["header"]
out["pagenums"] = d["page_numbers"]
''',
    "novel": '''
out["title"] = w.title_lbl.get_text()
out["chapters"] = len(w.chapters)
out["bodies"] = [w._buffer_text(c["buffer"]) for c in w.chapters]
out["ranges"] = [sorted(w._buffer_ranges(c["buffer"]).keys())
                 for c in w.chapters]
''',
    "screenplay": '''
d = w._collect_doc()
out["title"] = d["title"]
out["subtitle"] = d["subtitle"]
out["body"] = d["body"]
out["tags"] = sorted({s["tag"] for s in d["body_tags"]})
''',
    "journal": '''
out["n"] = len(w.entries)
out["text"] = w.entries[0]["text"] if w.entries else ""
out["title"] = w.entries[0]["title"] if w.entries else ""
out["tags"] = sorted({s.get("tag") for s in
                      (w.entries[0].get("tags") or [])}) if w.entries else []
''',
}

EXPECT = {
    "writer": lambda o: (
        o.get("body", "").startswith(MARK + " heading\nbody text here")
        and "bold" in o.get("tags", [])
        and "style:Heading 1" in o.get("tags", [])
        and "align:center" in o.get("tags", [])
        and o.get("tables") == 1
        and o.get("page") == "A4"
        and o.get("header") == MARK + " hdr"
        and o.get("pagenums") is True),
    "novel": lambda o: (
        o.get("title") == MARK + " Book"
        and o.get("chapters") == 2
        and MARK + " prose" in "".join(o.get("bodies", []))
        and "second chapter " + MARK in "".join(o.get("bodies", []))
        and any("italic" in r for r in o.get("ranges", []))),
    "screenplay": lambda o: (
        o.get("title") == MARK + " SCRIPT"
        and o.get("subtitle") == "by nobody"
        and MARK + " action line" in o.get("body", "")
        and o.get("tags")),
    "journal": lambda o: (
        o.get("n") == 1
        and o.get("text", "").startswith(MARK)
        and o.get("title") == MARK + " title line"
        and "bold" in o.get("tags", [])),
}

# ---------------------------------------------------------------------------
# The OTHER document on disk: the user file under Documents that File > Save
# writes and File > Open reads back. The session-recovery store above is the
# app's own; this one the person names, copies to a USB stick and mails to a
# publisher, so a lossy write or an Open that eats the open document is the same
# defect one level up. Written with the app's real writer, read with its real
# reader, in two separate processes.
# ---------------------------------------------------------------------------
FILE_WRITE = {
    "writer": '''
w.buf.set_text("%(M)s heading\\nbody text")
w.buf.select_range(w.buf.get_iter_at_offset(0), w.buf.get_iter_at_offset(7))
w._toggle_char("bold")
w._write_file(os.path.join(os.environ["NB_HOME"], "Documents", "d.writer"))
out["chip"] = w.save_chip.get_text()
''',
    "novel": '''
w.view.get_buffer().set_text("Chapter 1\\n%(M)s prose")
w._commit_manuscript_name("%(M)s Book")
out["ok"] = w._write_document(
    os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
''',
    "screenplay": '''
w.body.get_buffer().set_text("INT. HOUSE - DAY\\n%(M)s action")
w.scripttitle.set_text("%(M)s SCRIPT")
out["ok"] = w._write_file(
    os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
''',
}
FILE_READ = {
    "writer": '''
w._open_file(os.path.join(os.environ["NB_HOME"], "Documents", "d.writer"))
d = w._serialize()
out["body"] = d["body"]
out["bold"] = any(r[2] == "bold" for r in d["runs"])
out["path"] = os.path.basename(w._path or "")
''',
    "novel": '''
w._do_open_path(os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
out["title"] = w.title_lbl.get_text()
out["bodies"] = [w._buffer_text(c["buffer"]) for c in w.chapters]
out["path"] = os.path.basename(w.doc_path or "")
''',
    "screenplay": '''
out["ok"] = w._open_file(
    os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
out["title"] = w.scripttitle.get_text()
out["body"] = w.body.get_buffer().get_text(
    w.body.get_buffer().get_start_iter(),
    w.body.get_buffer().get_end_iter(), False)
out["path"] = os.path.basename(w._path or "")
''',
}
FILE_EXPECT = {
    "writer": lambda o: (o.get("body", "").startswith(MARK)
                         and o.get("bold") and o.get("path") == "d.writer"),
    "novel": lambda o: (o.get("title") == MARK + " Book"
                        and MARK + " prose" in "".join(o.get("bodies", []))
                        and o.get("path") == "d.json"),
    "screenplay": lambda o: (o.get("title") == MARK + " SCRIPT"
                             and MARK + " action" in o.get("body", "")
                             and o.get("path") == "d.json"),
}

# A user file damaged the same way the stores were. Opening it must not raise,
# and — the part that used to fail in Writer — must not blank the document that
# is already on screen and then leave the autosave to write that blank down.
BAD_FILE = {
    "writer": ("d.writer", {"version": 2, "body": "salvage me", "runs": "bold",
                            "page": "Letter", "images": [{"path": "/x"}]}),
    "novel": ("d.json", {"title": "Book", "chapters": "not a list"}),
    "screenplay": ("d.json", {"title": "S", "body": ["not a string"],
                              "body_tags": {}}),
}
BAD_OPEN = {
    "writer": '''
w.buf.set_text("%(M)s work in progress")
try:
    w._open_file(os.path.join(os.environ["NB_HOME"], "Documents", "d.writer"))
    out["raised"] = ""
except Exception as e:
    out["raised"] = repr(e)
d = w._serialize()
out["body"] = d["body"]
''',
    "novel": '''
w.view.get_buffer().set_text("Chapter 1\\n%(M)s work in progress")
try:
    w._do_open_path(os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
    out["raised"] = ""
except Exception as e:
    out["raised"] = repr(e)
out["body"] = "".join(w._buffer_text(c["buffer"]) for c in w.chapters)
''',
    "screenplay": '''
w.body.get_buffer().set_text("INT. X\\n%(M)s work in progress")
try:
    out["opened"] = w._open_file(
        os.path.join(os.environ["NB_HOME"], "Documents", "d.json"))
    out["raised"] = ""
except Exception as e:
    out["raised"] = repr(e)
b = w.body.get_buffer()
out["body"] = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
''',
}

# ---------------------------------------------------------------------------
# Controls that appear to work and change nothing (or change the wrong thing).
# Each case returns a dict the assertion below reads.
# ---------------------------------------------------------------------------
NOOP = {
    "writer": ('''
# Replace all against a document that was EDITED after the find ran. The match
# offsets are cached, and nothing re-ran the find, so the replacements used to
# land at stale positions and cut the prose apart mid-word.
w.buf.set_text("cat dog cat dog cat")
w.find_entry.set_text("cat")
w._do_find()
out["found"] = len(w._find_matches)
# the writer now types at the START of the document: every cached offset moves
w.buf.insert(w.buf.get_start_iter(), "XXXXXXXX ")
w.repl_entry.set_text("fox")
w._replace_all()
out["after_edit"] = w.buf.get_text(w.buf.get_start_iter(),
                                   w.buf.get_end_iter(), False)
# ...and on the FIRST press after typing a needle, with no find having run
w.buf.set_text("alpha beta alpha")
w._clear_find_highlight()
w.find_entry.set_text("alpha")
w.repl_entry.set_text("gamma")
w._replace_all()
out["first_press"] = w.buf.get_text(w.buf.get_start_iter(),
                                    w.buf.get_end_iter(), False)
''', lambda o: (o.get("found") == 3
                and o.get("after_edit") == "XXXXXXXX fox dog fox dog fox"
                and o.get("first_press") == "gamma beta gamma")),
    "novel": ('''
# The manuscript title is the MODEL, and the interface-translation layer must
# never rewrite it. "Notes" and "Contents" are catalog keys.
names = ["Notes", "Contents", "Body", "Quote", "Chapter 1", "My Great Novel"]
out["kept"] = []
for n in names:
    w._commit_manuscript_name(n)
    out["kept"].append([n, w.title_lbl.get_text(), w._serialize()["title"]])
# Find, then DELETE a chapter with the find bar still open. The hits name
# chapters by index and nothing re-ran the find, so "Next match" raised
# IndexError and the find buttons stayed dead for the rest of the session.
for t in ("Chapter 1\\nthe needle here", "Chapter 2\\nanother needle",
          "Chapter 3\\nthird needle"):
    w.view.get_buffer().set_text(t)
    pump()
    if not t.startswith("Chapter 3"):
        w._on_new_chapter()
        pump()
w._toggle_find(True)
w.find_entry.set_text("needle")
w._do_find()
out["hits"] = len(w._find_hits)
w._delete_chapter(2)
pump()
try:
    for _ in range(4):
        w._find_step(1)
    out["step_raised"] = ""
except Exception as e:
    out["step_raised"] = repr(e)
out["hits_after"] = len(w._find_hits)
''', lambda o: (all(n == shown == stored
                    for n, shown, stored in o.get("kept", [[1, 2, 3]]))
                and o.get("hits") == 3
                and o.get("step_raised") == ""
                and o.get("hits_after") == 2)),
}

fails = []
checks = [0]


def check(name, ok, detail=""):
    checks[0] += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  -- " + detail) if detail and not ok else ""))
    if not ok:
        fails.append(name)


def main():
    want = [a for a in sys.argv[1:]] or None
    root = tempfile.mkdtemp(prefix="writing-apps-selftest-")
    try:
        for app, cls, cfg in APPS:
            if want and app not in want:
                continue
            print("\n== %s ==" % app)

            # -- round trip: edit + close, then reopen in a fresh process ----
            home = os.path.join(root, app + "-rt")
            os.makedirs(os.path.join(home, ".config", "notebook"))
            ok, _o, err = run_worker(app, cls, home, EDIT[app])
            check("%s: edits applied and window closed" % app, ok, err)
            if ok:
                ok2, out, err2 = run_worker(app, cls, home, READBACK[app])
                check("%s: reopens after the edit" % app, ok2, err2)
                if ok2:
                    check("%s: document round-trips through save+reopen" % app,
                          EXPECT[app](out), json.dumps(out)[:220])

            # -- controls that silently do nothing / the wrong thing ---------
            if app in NOOP:
                script, expect = NOOP[app]
                home = os.path.join(root, app + "-noop")
                os.makedirs(os.path.join(home, ".config", "notebook"))
                ok, out, err = run_worker(app, cls, home, script)
                check("%s: controls do what they say" % app,
                      ok and expect(out), err or json.dumps(out)[:220])

            # -- the user file under Documents: write it, reopen it, diff ----
            if app in FILE_WRITE:
                home = os.path.join(root, app + "-file")
                os.makedirs(os.path.join(home, ".config", "notebook"))
                os.makedirs(os.path.join(home, "Documents"))
                ok, _o, err = run_worker(app, cls, home,
                                         FILE_WRITE[app] % {"M": MARK})
                check("%s: File > Save writes a user file" % app, ok, err)
                if ok:
                    ok2, out, err2 = run_worker(app, cls, home, FILE_READ[app])
                    check("%s: File > Open reads it back" % app, ok2, err2)
                    if ok2:
                        check("%s: user file round-trips through Save+Open"
                              % app, FILE_EXPECT[app](out),
                              json.dumps(out)[:200])

                # ...and a DAMAGED user file must not take the open document
                # with it. Writer used to blank the buffer and then raise.
                home = os.path.join(root, app + "-badfile")
                os.makedirs(os.path.join(home, ".config", "notebook"))
                docs = os.path.join(home, "Documents")
                os.makedirs(docs)
                name, data = BAD_FILE[app]
                with open(os.path.join(docs, name), "w") as fh:
                    json.dump(data, fh)
                ok, out, err = run_worker(app, cls, home,
                                          BAD_OPEN[app] % {"M": MARK})
                check("%s: opening a damaged user file does not crash" % app,
                      ok and not out.get("raised"),
                      err or out.get("raised", ""))
                if ok:
                    body = out.get("body", "")
                    check("%s: a damaged user file never leaves a blank page"
                          % app, bool(body.strip()), repr(body)[:120])

            # -- three open+close cycles over each damaged store -------------
            for label, data in damage_cases(app):
                home = os.path.join(root, "%s-%s" % (app, abs(hash(label))))
                cfgdir = os.path.join(home, ".config", "notebook")
                os.makedirs(cfgdir)
                with open(os.path.join(cfgdir, cfg), "w") as fh:
                    json.dump(data, fh)
                built = True
                lost_at = 0
                first = None
                for n in range(1, CYCLES + 1):
                    ok, _o, err = run_worker(app, cls, home)
                    if not ok:
                        built = False
                        check("%s: window builds on '%s'" % (app, label),
                              False, "open %d: %s" % (n, err))
                        break
                    left = survivors(home)
                    if first is None:
                        first = left
                    if not left and not lost_at:
                        lost_at = n
                if built:
                    check("%s: window builds on '%s'" % (app, label), True)
                    check("%s: '%s' survives %d open+close cycles"
                          % (app, label, CYCLES), not lost_at,
                          "destroyed by cycle %d (after #1 it was in %s)"
                          % (lost_at, ";".join(first or []) or "nowhere"))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n%d checks, %d failed" % (checks[0], len(fails)))
    if fails:
        for f in fails:
            print("  FAILED: " + f)
        print("RESULT: SOME FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
