#!/usr/bin/env python3
"""Display-free adversarial tail checks for the final seven app audit."""
import errno
import json
import os
import tempfile
import inspect
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))

import calculator
import g2048
import installer
import packages
import sysmon
import nbapp

failed = 0


def check(name, condition, evidence=""):
    global failed
    if condition:
        print("PASS", name, evidence)
    else:
        failed += 1
        print("FAIL", name, evidence)


def mutant(name, detected):
    check("PASS-MUTANT " + name, detected)


def guarded_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return True, json.load(fh)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


# calculator: evaluator edges plus forward-compatible dict-store law.
calc = calculator.Calculator.__new__(calculator.Calculator)
calc.deg, calc.variables, calc.ans, calc.fix = True, {}, 7, None
for expr, want in (("200+10%+10%", "242"), ("-50%", "-0.5")):
    calc.expr = expr
    check("calculator edge %s" % expr, calc.evaluate() == want,
          "got=%r" % calc.evaluate())
calc.expr = "1/0"
check("calculator division by zero is honest", calc.evaluate() == "Error")
damaged = calculator.sanitize_state({"ans": 3, "future": {"v": 9}})
check("calculator unknown keys survive in _extra",
      damaged.get("_extra") == {"future": {"v": 9}}, repr(damaged.get("_extra")))
mutant("calculator _extra verifier rejects dropped key",
       calculator.sanitize_state({"future": 1}).get("_extra") != {})

# g2048: damaged score/best validation and destructive new-game undo snapshot.
with tempfile.TemporaryDirectory() as td:
    old_state = g2048.STATE_FILE
    g2048.STATE_FILE = os.path.join(td, "g2048.json")
    with open(g2048.STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write('{"best": -3, "score": true, "board": [[2,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]]}')
    game = g2048.Game2048.__new__(g2048.Game2048)
    check("g2048 damaged best is rejected", game._load_best() == 0)
    check("g2048 boolean score is rejected", game._load_saved_game() is None)
    g2048.STATE_FILE = old_state
check("g2048 exposes undo for destructive new game", hasattr(g2048.Game2048, "undo_new_game"))
check("g2048 reset-best destruction has undo", hasattr(g2048.Game2048, "undo_reset_best"))
mutant("g2048 undo verifier rejects missing callback",
       getattr(g2048.Game2048, "undo_new_game", None) is not None)
try:
    css = open(g2048.__file__, encoding="utf-8").read()
    tile_ok = 'v in TILE_COLORS else "t-super"' in css and ".tile.t-super" in css
except Exception as exc:
    tile_ok, css = False, "%s: %s" % (type(exc).__name__, exc)
check("g2048 65536 uses the bounded super tile style", tile_ok, str(css)[:120])

# sysmon: classic comm trap and kill-failure honesty are pure executable checks.
hostile = "42 (name ) ( still) S " + " ".join(["0"] * 19 + ["987"])
rp = hostile.rfind(")")
check("sysmon hostile comm splits at final parenthesis",
      hostile[hostile.find("(") + 1:rp] == "name ) ( still")
check("sysmon EPERM never claims killed",
      "cannot be ended" in sysmon.SystemMonitor._end_problem("X", OSError(errno.EPERM, "no")))
mutant("sysmon honesty verifier rejects false success",
       "cannot be ended" not in "Ending X")

# packages: list-store truth is re-read before mutation; malformed data is not overwritten.
with tempfile.TemporaryDirectory() as td:
    prior_home = os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = td
    pkg = packages.Packages.__new__(packages.Packages)
    path = pkg._removed_apps_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(["Writer", "Writer", 7], fh)
    check("packages removed-app list always matches store",
          pkg._load_removed_apps() == {"Writer", "7"})
    mutant("packages verifier rejects stale list", pkg._load_removed_apps() != {"Writer"})
    if prior_home is None:
        os.environ.pop("NB_HOME", None)
    else:
        os.environ["NB_HOME"] = prior_home

# installer: exact fit accepted, one byte short refused, declined swap removed.
inst = installer.Installer.__new__(installer.Installer)
inst.payload_bytes = 900 * 1024 * 1024
inst.cfg = {"swap": True, "swap_mib": 8192}
need = inst._min_disk_bytes()
check("installer exact fit is accepted", not inst._disk_too_small(need))
check("installer one byte short is refused", inst._disk_too_small(need - 1))
inst.cfg["swap"] = False
check("installer declined swap leaves arithmetic", inst._min_disk_bytes() < need)
with tempfile.TemporaryDirectory() as td:
    inst._post_log = lambda *_: None
    inst._write_locale_json(td, "fr")
    locale_path = os.path.join(td, "root", ".config", "notebook", "locale.json")
    locale_ok, locale_data = guarded_json(locale_path)
    check("installer locale write is readable at desktop path",
          locale_ok and locale_data.get("keyboard") == "fr", repr(locale_data))
mutant("installer boundary verifier rejects one-byte-short", inst._disk_too_small(inst._min_disk_bytes() - 1))

# illustrator: executable source inspection pins the already-fixed overlay rule.
import illustrator
canvas_src = inspect.getsource(illustrator.Illustrator._canvas_size_prompt)
check("illustrator overlay callback reads captured state before destruction",
      'raw = {k: str(state[k]).strip()' in canvas_src
      and 'ent.get_text()' in canvas_src)
check("illustrator minimum canvas size remains selectable",
      illustrator.MIN_DIM <= 16 and "(16, 16)" in canvas_src)
mutant("illustrator prompt verifier rejects callback widget reads",
       'raw = {k: fields[k].get_text()' not in canvas_src)

# A failed store write has to reach the PERSON, and these four apps are the
# ones where it could not: each assigned its reason to nbapp.save_failure_reason
# — a FUNCTION, so the assignment told nobody and left the shared sentence
# producer replaced by a string for the rest of the process. The checks that
# stood here read that attribute back and reported it as the app "surfacing" a
# reason, which is how the defect survived nine apps. What a person can reach is
# checked instead: the sentence recorded on the app for its status line, one
# message in the notification centre for the save that failed while the window
# was already going away, and the producer still callable afterwards.
import nbnotify
original_atomic = nbapp.atomic_write_json
original_reason = nbapp.save_failure_reason
original_post = nbnotify.post
posted = []
def fail_write(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "audit disk full")
def capture_post(title, body="", app="", app_name="", icon=""):
    posted.append((title, body, app))
    return "captured"
nbapp.atomic_write_json = fail_write
nbnotify.post = capture_post
DISK_FULL = original_reason(OSError(errno.ENOSPC, "audit disk full"))


def told(win, label, save):
    """One failed save, asserted the way the person meets it."""
    before = len(posted)
    save()
    check(label + " records the reason for its status line",
          getattr(win, "_save_error", "") == DISK_FULL,
          repr(getattr(win, "_save_error", "")))
    mine = posted[before:]
    check(label + " leaves the reason in the notification centre",
          len(mine) == 1 and mine[0][1] == DISK_FULL, repr(mine))
    check(label + " keeps nbapp.save_failure_reason callable",
          callable(nbapp.save_failure_reason))
    # A full disk fails every autosave; the tray must not fill with one
    # repeated sentence, which is the failure a notification centre cannot
    # survive.
    again = len(posted)
    save()
    check(label + " says it once, not once per write",
          len(posted) == again, repr(posted[again:]))


try:
    calc._store_readable = True
    state = calculator.sanitize_state({})
    for key, value in state.items():
        if key != "_extra": setattr(calc, key, value)
    calc._extra = state["_extra"]
    told(calc, "calculator failed save", calc._save_prefs)

    game.best, game.board, game.score, game.status, game._extra = 4, [[2, 0, 0, 0]] + [[0]*4 for _ in range(3)], 0, "play", {}
    told(game, "g2048 failed save", game._save_best)

    # Packages is the one of the four where a person is looking straight at the
    # result, so the inspector has to say it too — and the listing must not go
    # on showing an application as removed when nothing recorded that it was.
    pkg.sel = next(i for i, row in enumerate(packages.PACKAGES)
                   if row[packages.KIND] == "Application")
    removed_name = packages.PACKAGES[pkg.sel][packages.NAME]
    pkg._load_removed_apps = lambda: set()
    pkg._rebuild_detail = lambda: None
    flashes = []
    pkg._flash = lambda text, err=False: flashes.append((text, err))
    told(pkg, "packages failed save", lambda: pkg._set_app_removed(True))
    check("packages says a failed removal in the inspector",
          flashes and flashes[0][0] == DISK_FULL and flashes[0][1],
          repr(flashes))
    check("packages does not show an application as removed after a failed write",
          removed_name not in pkg._removed_apps, repr(pkg._removed_apps))
    # THE RELIABLE HALF. nbnotify.post writes its record through
    # atomic_write_json — to the disk that is full, the filesystem that is
    # read-only, the quota that is exhausted. So in the exact case this
    # mechanism exists for, the tray usually cannot be reached, while the
    # reason on the window still can. A status line reads the reason; the tray
    # is a courtesy.
    #
    # MODELLED BY MAKING post() RAISE, not by chmod. My first fixture made the
    # spool unwritable and could not be reddened by any sabotage — because this
    # block runs while atomic_write_json is ALREADY patched to fail, so nothing
    # reached the spool either way and the permissions never mattered. The
    # fixture has to isolate the property from the state around it.
    def exploding_post(*_a, **_k):
        raise OSError(errno.ENOSPC, "the tray is on the full disk too")

    nbnotify.post = exploding_post
    try:
        stranded = calculator.Calculator.__new__(calculator.Calculator)
        reason = nbapp.note_save_failure(stranded, OSError(errno.ENOSPC, "full"),
                                         "state.json")
        check("a failed save records its reason even when the tray cannot be written",
              getattr(stranded, "_save_error", "") == DISK_FULL, repr(reason))
        check("...and the caller is handed the sentence regardless",
              reason == DISK_FULL, repr(reason))
    except Exception as exc:                                      # noqa: BLE001
        check("a failed save records its reason even when the tray cannot be written",
              False, "note_save_failure raised: %r" % (exc,))
        check("...and the caller is handed the sentence regardless", False, "raised")
    finally:
        nbnotify.post = capture_post

finally:
    nbapp.atomic_write_json = original_atomic
    nbapp.save_failure_reason = original_reason
    nbnotify.post = original_post

print("RESULT: %s" % ("FAILED: %d checks" % failed if failed else "PASS"))
raise SystemExit(1 if failed else 0)
