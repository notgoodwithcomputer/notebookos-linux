#!/usr/bin/env python3
"""Open and close a damaged store SEVERAL TIMES, and prove the user's data is
still recoverable at the end.

THE BUG THIS EXISTS FOR, and why every other suite missed it: the damaged-store
tests open the app once. For 13 of the 28 stores here, one open+close was enough
to blank the store -- the user's bytes survived only because nbapp's
preserve_damaged had copied them to <store>.bak on the way past. That is a pass,
and it is what data_safety_selftest measures.

Nobody measured the SECOND open. It is a fresh process, so nbapp._BACKED_UP is
empty again, and the store parses now (it holds the blank state the first close
wrote), so preserve_damaged took the "keep one previous-good copy" branch a
second time and wrote the BLANK store over the .bak -- the only remaining copy.
Two opens, two closes, no user action at all, and an address book, a diary, a
manuscript, a game project or a year of playlists was gone for good.

The store shape used below is valid JSON that no app recognises, which is the
case that reaches this path: a file that does not PARSE is quarantined under
<store>.damaged-<stamp> and never overwritten, but valid JSON of the wrong shape
parses perfectly and reads as "no data".

  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/reopen_damage_selftest.py [app]

ONE OPEN PER PROCESS, always: _BACKED_UP is module state, so driving the cycles
inside one process is exactly the lie this suite exists to catch.
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))

CYCLES = 3
MARK = "USERDATA-MARKER-9Q7"

# Valid JSON, no app's shape. The keys are the ones the persisting apps actually
# read, so each app gets far enough to decide it has nothing and open blank.
WRONG = json.dumps({k: MARK for k in (
    "entries", "items", "tasks", "chapters", "body", "tx", "recipes", "people",
    "events", "notes", "history", "board", "best", "roms", "layers", "log")})

# Every store a person can put their own work into.
APPS = [
    ("journal", "journal.json"), ("accounting", "accounting.json"),
    ("cookbook", "cookbook.json"), ("contacts", "contacts.json"),
    ("tasks", "tasks-app.json"), ("calendar", "calendar.json"),
    ("ebook", "ebook.json"), ("music", "music.json"),
    ("novel", "novel.json"), ("screenplay", "screenplay.json"),
    ("sequencer", "sequencer.json"), ("video", "video.json"),
    ("writer", "writer.json"), ("academics", "academics.json"),
    ("illustrator", "illustrator.json"), ("maps", "maps.json"),
    ("media", "media.json"), ("workout", "workout.json"),
    ("mealplanner", "mealplanner.json"), ("language", "language.json"),
    ("widgetsettings", "widgets.json"), ("finder", "finder.json"),
    ("settings", "settings.json"), ("terminal", "terminal.json"),
    ("calculator", "calculator.json"), ("g2048", "g2048.json"),
    ("gbaemu", "gbaemu.json"), ("gbasdk", "gbasdk.json"),
]

# Launch the app the way the Finder does and close it the way Esc does. _APP_DIR
# is repointed per process: nbapp.claim_single_instance() calls os._exit(0) when
# it finds a live registration in the shared /tmp/nb-apps, which would end this
# worker with no output and status 0 -- a silent false pass.
WORKER = r'''
import os, sys, inspect
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
mod = __import__(sys.argv[1])
cls = None
for _n, c in inspect.getmembers(mod, inspect.isclass):
    if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
        cls = c
        break
if cls is None:
    print("NOCLASS")
    raise SystemExit(2)


def pump():
    for _ in range(6):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


w = cls()
pump()
w.destroy()
pump()
print("RAN")
'''


def survivors(home):
    """Every file under `home` that still contains the user's bytes."""
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


def run_app(app, cfgname, root):
    home = os.path.join(root, app)
    cfgdir = os.path.join(home, ".config", "notebook")
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, cfgname), "w") as fh:
        fh.write(WRONG)

    first = None
    for n in range(1, CYCLES + 1):
        env = dict(os.environ, NB_HOME=home, DISPLAY=os.environ.get("DISPLAY", ":0"),
                   PYTHONPATH=DE + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c", WORKER, app],
                           capture_output=True, text=True, timeout=180, env=env)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().splitlines()
            return False, "open %d did not launch: %s" % (
                n, err[-1][:90] if err else "?")
        left = survivors(home)
        if first is None:
            first = left
        if not left:
            return False, ("destroyed by open+close #%d (after #1 it was in %s)"
                           % (n, ";".join(first) or "nowhere"))
    return True, ";".join(first)


def main():
    want = sys.argv[1:] or None
    apps = [(a, c) for a, c in APPS if want is None or a in want]
    root = tempfile.mkdtemp(prefix="reopen_damage_")
    bad = []
    try:
        for app, cfgname in apps:
            ok, detail = run_app(app, cfgname, root)
            print("%s %-16s %s" % ("PASS" if ok else "FAIL", app, detail))
            if not ok:
                bad.append(app)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("")
    print("%d stores, %d survived %d open+close cycles, %d LOST"
          % (len(apps), len(apps) - len(bad), CYCLES, len(bad)))
    if bad:
        print("RESULT: SOME FAILED  (" + ", ".join(bad) + ")")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
