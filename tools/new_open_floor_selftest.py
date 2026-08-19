#!/usr/bin/env python3
"""File > New and File > Open must not be able to lose an unsaved document.

THE HOLE. The campaign retired the "discard this manuscript?" question in
favour of undo (8ddfd945), and confirm_undo_adversarial_selftest FORBIDS
reinstating it. But undo lives only as long as the window: press New by
mistake, close the app, reopen it, and an afternoon's writing is gone — no
question asked and nothing on disk to go back to. The real-use drive found it
(novel F3) and an independent verifier confirmed it as DATA LOSS.

The fix is a floor rather than a question: an unsaved, UNBOUND manuscript that
holds something is written into Documents as a real manuscript file, under its
own title, before New or Open replaces it — and the save chip says where it
went. A manuscript that already has a file of its own is untouched (it is
already on disk), and an empty one writes nothing (there is nothing to keep).

    tools/guestrun.sh python3 tools/novel_new_open_floor_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = tempfile.mkdtemp(prefix="nb-novel-floor-")
os.environ["NB_DRIVE_HOME_ROOT"] = ROOT

import appdrive  # noqa: E402
from gi.repository import Gtk  # noqa: E402

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def fresh(tag):
    home = os.path.join(ROOT, tag)
    shutil.rmtree(home, ignore_errors=True)
    return appdrive.Drive("novel", home=home), home


def docs_of(home):
    d = os.path.join(home, "Documents")
    return sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []


def write_some(d, text, title=None):
    tv = d.find(Gtk.TextView)[0]
    tv.grab_focus()
    d.pump()
    d.type(text)
    d.pump(0.5)
    if title is not None:
        d.app._set_title(title)
        d.pump(0.2)


def kept_text(home, name):
    with open(os.path.join(home, "Documents", name)) as fh:
        return json.dumps(json.load(fh))


def main():
    # ---- 1. File > New on an unsaved book keeps it ------------------------
    d, home = fresh("new")
    try:
        write_some(d, "The lighthouse keeper counted seven ships.", "Winter Ships")
        check("an unsaved manuscript with writing in it counts as content",
              d.app._has_content() and d.app.doc_path is None)
        d.app._do_file_new()
        d.pump(0.6)
        files = [f for f in docs_of(home) if f.endswith(".json")]
        check("File > New writes the outgoing manuscript into Documents",
              len(files) == 1, repr(docs_of(home)))
        check("...naming the file after the book, not 'untitled'",
              bool(files) and files[0].startswith("Winter Ships"), repr(files))
        check("...and the file really holds the writing",
              bool(files) and "lighthouse keeper" in kept_text(home, files[0]))
        # The chip is transient — the next autosave rewrites it — so the
        # durable half is the notification, which is what the person still
        # has when they look up. Both are checked; only the notification is
        # allowed to be the one that lasts.
        spool = os.path.join(home, ".config", "notebook", "notifications")
        posted = []
        for root, _dirs, names in os.walk(spool):
            for n in names:
                try:
                    with open(os.path.join(root, n)) as fh:
                        posted.append(json.load(fh))
                except Exception:                                 # noqa: BLE001
                    pass
        check("...and the notification centre says where it went",
              any("Documents" in json.dumps(p) and files[0] in json.dumps(p)
                  for p in posted), repr(posted)[:200])
        check("...while the new manuscript really is blank",
              d.app._total_words == 0 and d.app.doc_path is None)
    finally:
        d.close()

    # ---- 2. an EMPTY manuscript writes nothing ----------------------------
    d, home = fresh("empty")
    try:
        d.app._do_file_new()
        d.pump(0.4)
        check("New on an empty manuscript keeps no file (nothing to keep)",
              [f for f in docs_of(home) if f.endswith(".json")] == [],
              repr(docs_of(home)))
    finally:
        d.close()

    # ---- 3. a BOUND manuscript is left alone ------------------------------
    d, home = fresh("bound")
    try:
        write_some(d, "Already saved somewhere.", "Bound Book")
        os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
        bound = os.path.join(home, "Documents", "Bound Book.json")
        d.app._write_document(bound)
        d.app.doc_path = bound
        before = docs_of(home)
        d.app._do_file_new()
        d.pump(0.4)
        check("New on a manuscript that already has a file writes no copy",
              docs_of(home) == before, "%r -> %r" % (before, docs_of(home)))
    finally:
        d.close()

    # ---- 3b. a bound manuscript typed into AFTER its last save ------------
    # The hole the first floor left: "it has a file, so it is already on disk"
    # stopped being true the moment the writer typed one more sentence. New
    # replaces the model AND the recovery store, so those words lived nowhere.
    d, home = fresh("behind")
    try:
        write_some(d, "Saved this much.", "Tea")
        os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
        bound = os.path.join(home, "Documents", "Tea.json")
        d.app._write_document(bound)
        d.app.doc_path = bound
        before = docs_of(home)
        write_some(d, " Then Thursday happened.")
        check("a manuscript typed into after its save reads as behind its file",
              d.app._file_behind())
        d.app._do_file_new()
        d.pump(0.6)
        kept = [f for f in docs_of(home)
                if f.endswith(".json") and f not in before]
        check("New on a bound manuscript with unsaved writing keeps a copy",
              len(kept) == 1, "%r -> %r" % (before, docs_of(home)))
        check("...holding the words written since the last save",
              bool(kept) and "Thursday happened" in kept_text(home, kept[0]))
        check("...and the file it was bound to is left exactly as it was",
              "Thursday happened" not in open(bound).read())
    finally:
        d.close()

    # ---- 4. File > Open keeps the outgoing book too -----------------------
    d, home = fresh("open")
    try:
        write_some(d, "Chapter one, in which nothing is saved.", "Draft One")
        os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
        other = os.path.join(home, "Documents", "Other.json")
        # a real manuscript document to open, written by the app itself
        d.app._write_document(other)
        # ...then make the on-screen book different and still unbound
        write_some(d, " Another line only on screen.")
        d.app.doc_path = None
        d.app._do_open_path(other)
        d.pump(0.6)
        kept = [f for f in docs_of(home)
                if f.endswith(".json") and f.startswith("Draft One")]
        check("File > Open writes the outgoing manuscript into Documents",
              len(kept) == 1, repr(docs_of(home)))
        check("...holding what was on screen, not what was opened",
              bool(kept) and "only on screen" in kept_text(home, kept[0]))
    finally:
        d.close()

    # ---- 5. SCREENPLAY has the same exposure and the same floor ----------
    home = os.path.join(ROOT, "sp")
    shutil.rmtree(home, ignore_errors=True)
    d = appdrive.Drive("screenplay", home=home)
    try:
        d.app.body.get_buffer().set_text("INT. KITCHEN - NIGHT\nShe reads it twice.")
        d.app.scripttitle.set_text("The Letter")
        d.pump(0.4)
        d.app._file_new()
        d.pump(0.6)
        kept = [f for f in docs_of(home) if f.endswith(".json")]
        check("Screenplay: File > New writes the outgoing script into Documents",
              len(kept) == 1 and kept[0].startswith("The Letter"), repr(docs_of(home)))
        check("...holding the pages that were on screen",
              bool(kept) and "reads it twice" in kept_text(home, kept[0]))
        check("...and the new page really is blank", d.app._is_empty())
    finally:
        d.close()

    # ---- 5b. screenplay, bound and typed into after its last save --------
    home = os.path.join(ROOT, "sp-behind")
    shutil.rmtree(home, ignore_errors=True)
    d = appdrive.Drive("screenplay", home=home)
    try:
        d.app.body.get_buffer().set_text("INT. KITCHEN - NIGHT\nShe reads it once.")
        d.app.scripttitle.set_text("Tea")
        d.pump(0.4)
        os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
        bound = os.path.join(home, "Documents", "Tea.json")
        d.app._write_file(bound)
        d.app._path = bound
        d.app._file_dirty = False
        before = docs_of(home)
        d.app.body.get_buffer().set_text(
            "INT. KITCHEN - NIGHT\nShe reads it once.\nThen Thursday happened.")
        d.pump(0.4)
        check("Screenplay: typing after a save marks the script behind its file",
              d.app._file_dirty)
        d.app._file_new()
        d.pump(0.6)
        kept = [f for f in docs_of(home)
                if f.endswith(".json") and f not in before]
        check("Screenplay: New on a bound script with unsaved pages keeps a copy",
              len(kept) == 1, "%r -> %r" % (before, docs_of(home)))
        check("...holding the pages written since the last save",
              bool(kept) and "Thursday happened" in kept_text(home, kept[0]))
    finally:
        d.close()

    home = os.path.join(ROOT, "sp-empty")
    shutil.rmtree(home, ignore_errors=True)
    d = appdrive.Drive("screenplay", home=home)
    try:
        d.app._file_new()
        d.pump(0.4)
        check("Screenplay: New on an empty page keeps no file",
              [f for f in docs_of(home) if f.endswith(".json")] == [],
              repr(docs_of(home)))
    finally:
        d.close()

    shutil.rmtree(ROOT, ignore_errors=True)
    print("%d checks, %d passed, %d FAILED" % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
