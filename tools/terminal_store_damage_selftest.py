#!/usr/bin/env python3
"""terminal: a SCALAR / non-object prefs store is preserved across open+close.

THE BUG (same class as g2048; the data-loss-read-side worst class): terminal.json
holding a bare JSON scalar -- a corrupt or foreign store -- is destroyed on the
SECOND open+close. _load_prefs reads nothing from a non-dict and returns the
defaults; the close-time _save_prefs then writes those defaults over it. On open
#1 preserve_damaged has copied the bytes to <store>.bak, so open #1 passes -- but
the default prefs (font_scale 1.0 + cursor_blink true, weight 2) OUTWEIGH a bare
marker (weight 1), so on open #2 _bak_would_shrink refreshes the .bak over the
user's bytes.

reopen_damage_selftest plants a wrong-shape DICT, whose unknown keys terminal
rides through _extra and writes back, so it survives -- the scalar was never
tested. The fix quarantines a non-object store at load
(nbapp.quarantine_unrecognized), before any save.

Three open+close cycles, each a fresh process (the loss needs the once-per-
process _BACKED_UP guard reset; open #1 always passes). This host has VTE, so
_save_prefs is live -- without it the save would no-op and the test be vacuous.
Point TERMINAL_MODULE_DIR at a copy with the guard removed to red-proof: the
marker is gone by cycle 2.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = str(ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("TERMINAL_MODULE_DIR", DE)
MARK = "USERDATA-MARKER-TERM-K7"
PAYLOAD = json.dumps(MARK)          # a bare JSON string: valid JSON, not a dict
CYCLES = 3
CHECKS = 0
FAILS = []

# Launch terminal the way the Finder does and close it the way Esc does (destroy
# flushes _save_prefs). _APP_DIR is repointed per process so
# claim_single_instance can't os._exit(0) this worker on a stale registration
# and launder a crash as a silent pass. If the VTE backend is missing the worker
# says so (SKIP), because _save_prefs no-ops without a live terminal and the
# test would then pass vacuously.
WORKER = r'''
import os, sys, inspect
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
import terminal
if terminal.Vte is None:
    print("SKIP-NOVTE"); raise SystemExit(0)
cls = None
for _n, c in inspect.getmembers(terminal, inspect.isclass):
    if c.__module__ == "terminal" and issubclass(c, Gtk.Window):
        cls = c
        break
if cls is None:
    print("NOCLASS"); raise SystemExit(2)


def pump():
    for _ in range(6):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


w = cls()
pump()
if getattr(w, "term", None) is None:
    print("SKIP-NOVTE"); raise SystemExit(0)
w.destroy()
pump()
print("RAN")
'''


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s%s" % (name, (": " + detail) if detail else ""))


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


def one_cycle(app_home):
    env = dict(os.environ, NB_HOME=app_home,
               DISPLAY=os.environ.get("DISPLAY", ":0"),
               PYTHONPATH=MODULE_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", WORKER],
                       capture_output=True, text=True, timeout=180, env=env)
    out = (r.stdout or "")
    if "SKIP-NOVTE" in out:
        return "skip", "no VTE backend"
    ok = r.returncode == 0 and "RAN" in out
    err = (r.stderr or out).strip().splitlines()
    return ok, (err[-1][:100] if err else "?")


def main():
    print("terminal from: %s" % MODULE_DIR)
    root = tempfile.mkdtemp(prefix="terminal_store_damage_")
    home = os.path.join(root, "terminal")
    cfgdir = os.path.join(home, ".config", "notebook")
    store = os.path.join(cfgdir, "terminal.json")
    os.makedirs(cfgdir, exist_ok=True)
    try:
        with open(store, "w", encoding="utf-8") as fh:
            fh.write(PAYLOAD)
        check("the planted store is a bare scalar, not terminal's shape",
              not isinstance(json.loads(PAYLOAD), dict))

        launched_all = True
        survived_all = True
        first_seen = None
        for n in range(1, CYCLES + 1):
            ok, detail = one_cycle(home)
            if ok == "skip":
                print("SKIP: %s — the loss can't manifest without a live "
                      "terminal; run on a host with VTE." % detail)
                return 0
            if not ok:
                launched_all = False
                check("open+close #%d launches terminal" % n, False, detail)
                break
            left = survivors(home)
            if first_seen is None:
                first_seen = left
            if not left:
                survived_all = False
                check("the user's bytes survive open+close #%d" % n, False,
                      "destroyed at cycle %d (after #1 they were in %s)"
                      % (n, ";".join(first_seen) or "nowhere"))
                break
            check("the user's bytes survive open+close #%d" % n, True,
                  ";".join(left))

        if launched_all and survived_all:
            damaged = glob.glob(store + ".damaged-*")
            in_damaged = any(MARK.encode() in open(p, "rb").read()
                             for p in damaged)
            check("the scalar is quarantined to a .damaged-<stamp> file",
                  in_damaged, "damaged siblings: %r" % damaged)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
