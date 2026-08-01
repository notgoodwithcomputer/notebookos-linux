#!/usr/bin/env python3
"""
Headless CRASH-RESILIENCE selftest for every Notebook OS app.

The single highest-value regression test in the suite: it proves that no app
crashes on launch because of a missing or corrupt on-disk config.  A malformed
$NB_HOME/.config/notebook/<app>.json (or none at all) must never take an app
down — the app must fall back to its empty / safe state and open normally.
This is exactly the class of bug that produced the E-book reader's
"No module named xml" launch crash and would catch any future variant.

For EACH of the 24 apps, and for EACH of four config states —
  * no-config       : nothing on disk
  * bare-list       : a bare JSON list   [1, 2, 3]
  * truncated-json  : a syntactically invalid / truncated JSON blob
  * wrong-types     : a dict whose values are the wrong types
— the module is imported FRESH (del sys.modules) with NB_HOME pointed at a temp
dir and the garbage already in place, its Gtk.Window subclass is constructed,
the Gtk event queue is pumped, and the window destroyed.  Any exception raised
along the way is a FAIL; a clean construct is a PASS.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/tmp/rt python3 config_resilience_selftest.py

NB_HOME in the environment is ignored — the test manages its own throwaway
homes so it never touches the caller's real config.

Each case runs in its OWN SUBPROCESS (this file re-invoked with --worker).
That is how apps actually start on the guest, and it is required for
correctness here: a re-import in one process cannot re-register a class that
declares __gtype_name__ (GType names are global and permanent for the life of
the process), so settings.py's ReadingColumn made cases 2-4 of `settings` fail
with "could not create new GType" — a harness artifact that was masking whether
settings survives a corrupt config at all.  In a fresh process it does.
"""
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# Every app ships as <name>.py exposing exactly one Gtk.Window subclass and
# (when it persists) reads $NB_HOME/.config/notebook/<name>.json.
APPS = [
    "writer", "novel", "journal", "academics", "screenplay", "ebook",
    "cookbook", "contacts", "accounting", "calendar", "music",
    "illustrator", "sequencer", "video", "media", "g2048",
    "packages", "settings", "sysmon", "calculator", "terminal", "tasks",
    "language", "maps", "finder",
    "gbasdk", "gbaemu", "workout",
    # Not in the Applications folder (it opens from the desktop's right-click
    # menu), but it reads a store like any app and must survive a bad one.
    "widgetsettings",
    # Cookbook's sister app: reads cookbook.json read-only and writes
    # its own plan, so BOTH stores can be damaged independently.
    "mealplanner",
]

# Shared modules an app pulls in; drop them too so a re-import re-reads NB_HOME.
_SHARED = ("nbapp", "nbicons", "widgets", "splash")

# The four config states each app must survive.  Each entry is
# (case-name, writer) where writer(path) either creates a garbage file or
# leaves the slot empty (no-config).
_WRONG_TYPES = {
    # keys the persisting apps actually read, deliberately mistyped: lists
    # become scalars/strings, scalars become containers, records become lists
    # of non-dicts.  A naive `for x in data["k"]` / `x["field"]` blows up on
    # every one of these; a hardened loader shrugs and opens empty.
    "entries": "not-a-list",
    "items": 42,
    "tasks": None,
    "events": {"unexpected": "dict-not-list"},
    "records": ["string-not-dict", 123, None],
    "books": "nope",
    "conversations": 3.14,
    "people": {"k": "v"},
    "recipes": True,
    "playlists": 0,
    "transactions": "xyz",
    "calendars": 7,
    "notes": [[1, 2], {"bad": 1}],
    "balance": "NaN",
    "version": [1, 2, 3],
    "settings": "flat-string",
    # Workout's per-day goal snapshot: a map of date -> int, so the damage
    # worth testing is both a non-map and a map full of the wrong things.
    "goals": ["2026-01-01", None],
}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


CASES = [
    ("no-config", None),
    ("bare-list", lambda p: _write(p, "[1, 2, 3]")),
    ("truncated-json",
     lambda p: _write(p, '{"books": [ {"path": "x", "title": ')),
    ("wrong-types", lambda p: _write(p, json.dumps(_WRONG_TYPES))),
]

results = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    results.append(bool(ok))


def find_window_cls(mod):
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    return None


def pump():
    for _ in range(4):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def worker(app, home):
    """--worker body: import `app` against NB_HOME=home, construct its window,
    pump, destroy.  Prints OK or the traceback; exit status carries the verdict.
    Runs in its own process so GType registration and GTK state start clean."""
    os.environ["NB_HOME"] = home
    win = None
    try:
        mod = __import__(app)
        cls = find_window_cls(mod)
        if cls is None:
            print("no Gtk.Window subclass in module")
            return 1
        win = cls()
        pump()
        print("OK")
        return 0
    except Exception as e:
        print("%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc()))
        return 1
    finally:
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        pump()


def run_case(app, case_name, writer, home):
    """Return (ok, detail).  ok is True when the app imports + constructs +
    destroys without raising for this config state."""
    cfg_dir = os.path.join(home, ".config", "notebook")
    cfg_path = os.path.join(cfg_dir, app + ".json")
    # Clean slate: wipe any prior config, then lay down this case's garbage.
    shutil.rmtree(cfg_dir, ignore_errors=True)
    if writer is not None:
        writer(cfg_path)

    env = dict(os.environ, NB_HOME=home)
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--worker", app, home],
                           capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return False, "timed out constructing the window"
    if r.returncode == 0:
        return True, ""
    return False, (r.stdout + r.stderr).strip() or ("exit %d" % r.returncode)


def main():
    # Our own throwaway homes — never the caller's NB_HOME.
    saved_home = os.environ.get("NB_HOME")
    root = tempfile.mkdtemp(prefix="cfg_resilience_")
    try:
        for app in APPS:
            for case_name, writer in CASES:
                home = os.path.join(root, app, case_name)
                os.makedirs(home, exist_ok=True)
                ok, detail = run_case(app, case_name, writer, home)
                check("%s [%s]" % (app, case_name), ok)
                if not ok and detail:
                    for line in detail.rstrip().splitlines():
                        print("    | " + line)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if saved_home is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = saved_home

    ok = all(results)
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(worker(sys.argv[2], sys.argv[3]))
    raise SystemExit(main())
