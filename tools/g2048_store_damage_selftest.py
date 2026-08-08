#!/usr/bin/env python3
"""g2048: a SCALAR / non-object store is preserved across repeated open+close.

THE BUG (verified by the motion + app-improve lanes; the data-loss-read-side
worst class): g2048.json holding a bare JSON scalar -- a corrupt or foreign
store -- is destroyed on the SECOND open+close, with no user action.
preserve_damaged copies the bytes to <store>.bak on open #1, so open #1 passes;
but a fresh new-game board carries two spawned tiles and OUTWEIGHS a scalar
marker (weight 2 vs 1), so on open #2 _bak_would_shrink judges the blank game
the fuller copy and refreshes the .bak over the user's bytes -- gone.

WHY reopen_damage_selftest missed it: that gate plants a wrong-shape DICT, whose
unknown keys g2048 rides through _extra and writes back, so it survives. Only a
NON-object store loses, and the scalar payload is not (yet) in that gate.

THE FIX: _quarantine_unrecognized_store moves a non-dict store to
<store>.damaged-<stamp> at load, before any save, the way journal/calculator do.

Three open+close cycles, each a fresh process, because the loss needs the
once-per-process _BACKED_UP guard reset (open #1 always passes). Point
G2048_MODULE_DIR at a copy with the guard removed to red-proof: the marker is
gone by cycle 2.
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
MODULE_DIR = os.environ.get("G2048_MODULE_DIR", DE)
MARK = "USERDATA-MARKER-2048-K3"
PAYLOAD = json.dumps(MARK)          # a bare JSON string: valid JSON, not g2048's
CYCLES = 3
CHECKS = 0
FAILS = []

# Launch g2048 the way the Finder does and close it the way Esc does (destroy
# runs _on_destroy -> _save_best, the write that overwrites the store). _APP_DIR
# is repointed per process so claim_single_instance can't os._exit(0) this
# worker on a stale registration and launder a crash as a silent pass.
WORKER = r'''
import os, sys, inspect
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
import g2048
cls = None
for _n, c in inspect.getmembers(g2048, inspect.isclass):
    if c.__module__ == "g2048" and issubclass(c, Gtk.Window):
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
    """Every file under `home` that still holds the user's bytes."""
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
    ok = r.returncode == 0 and "RAN" in r.stdout
    err = (r.stderr or r.stdout or "").strip().splitlines()
    return ok, (err[-1][:100] if err else "?")


def main():
    print("g2048 from: %s" % MODULE_DIR)
    root = tempfile.mkdtemp(prefix="g2048_store_damage_")
    home = os.path.join(root, "g2048")
    cfgdir = os.path.join(home, ".config", "notebook")
    store = os.path.join(cfgdir, "g2048.json")
    os.makedirs(cfgdir, exist_ok=True)
    try:
        with open(store, "w", encoding="utf-8") as fh:
            fh.write(PAYLOAD)
        check("the planted store is a bare scalar, not g2048's shape",
              not isinstance(json.loads(PAYLOAD), dict))

        launched_all = True
        survived_all = True
        first_seen = None
        for n in range(1, CYCLES + 1):
            ok, detail = one_cycle(home)
            if not ok:
                launched_all = False
                check("open+close #%d launches g2048" % n, False, detail)
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

        # The mechanism, not just the luck: after the first open the scalar must
        # be at a .damaged-<stamp> sibling (quarantined), not merely clinging to
        # the .bak that cycle 2 is about to overwrite.
        if launched_all and survived_all:
            damaged = [p for p in glob.glob(store + ".damaged-*")]
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
