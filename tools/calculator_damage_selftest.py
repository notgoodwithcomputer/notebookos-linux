#!/usr/bin/env python3
"""A calculator.json this app cannot read must outlive being opened.

    tools/guestrun.sh python3 tools/calculator_damage_selftest.py

store_damage_selftest listed this app as "defended-untested: several shapes not
rewritten at all; the tape; needs a gate". The defence was real but it was the
WRONG defence, and measuring it turned up two faults that had been shipping.

FAULT 1 -- the store that parses but is not an object. calculator.json holding a
JSON array or a bare string is valid JSON, so nbapp.preserve_damaged waves it
through to its "keep one previous-good copy" branch and writes a .bak. The app
reads `isinstance(data, dict)` as False, opens blank, and the close-time flush
writes that blank over the file. On the SECOND open the .bak is refreshed from
the blank state and the last copy is gone. Measured on the module as it stood:
a store with three variables and a tape saved as a bare string was destroyed by
open+close #2, with no user action and no message.

WHY ONE CYCLE IS NOT A TEST, and the reason this suite runs three: after cycle 1
the bytes are sitting in calculator.json.bak, so a suite that opens the app once
and greps the config directory PASSES on the broken code. The loss is only
visible on the cycle that overwrites the backup. reopen_damage_selftest knows
this -- it is the bug that suite exists for -- but its payload is a wrong-shape
OBJECT, and an object of the user's real keys outweighs the blank default under
_bak_would_shrink, so it never overwrites and the app passes honestly. Whether
this class of loss reproduces depends on the WEIGHT of the payload, not on the
app: array survived, bare string did not.

FAULT 2 -- the cure that costs the session. The old loader set
`_store_readable = False` on any parse failure and `_save_prefs` returned early
for the rest of the run. The damaged file survived, and so did nothing else:
everything the person did afterwards was dropped at close, silently.
contacts.py:494 records this exact cure shipping in journal and the save-failure
gate catching it. save_failure_selftest.py has no calculator case, which is why
it was still here.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY of calculator.py,
suite pointed at it with CALCULATOR_MODULE_DIR. The counts below are MEASURED,
and three of the five disagree with what I expected before running them:

  5. THE REGRESSION -- the loader restored to the body that shipped
                                                              14 of 23 FAILED
       FAIL string: bytes survive 3 open+close cycles  <- LOST on cycle 2
       ...all five "says so", all three "work is saved", every ".damaged- file"
       FAIL empty: the file is moved aside, not deleted
     and `array: bytes survive 3 open+close cycles` stays GREEN. That green is
     the whole finding: the same defect destroys a bare-string store and spares
     an array one, because _bak_would_shrink compares payload weight and an
     array of the user's real keys outweighs the blank default. A suite planting
     one wrong-shape payload would have called this app defended.

  1. the wrong-shape branch stops quarantining
     (`self._damaged_path = nbapp.quarantine_unrecognized(STATE_FILE)`
      -> `self._damaged_path = None`)                                2 FAILED
       FAIL array:  and they are in a .damaged- file, not just the .bak
       FAIL string: and they are in a .damaged- file, not just the .bak
     I expected six and got two, and the four that stayed green are right to be
     green: with no quarantine path recorded, `_store_readable` goes False and
     the app falls back to the OLD cure -- it refuses to save, so the bytes do
     survive and the notice does show. It buys that by throwing the session
     away, which is what proof 2 measures. The two checks that fail are the two
     that can tell the difference, which is why the ".damaged- file, not just
     the .bak" half of each pair is worth its line.

  2. the parse-failure branch restores the session-wide save gate
     (`self._store_readable = (self._damaged_path is not None ...)`
      -> `self._store_readable = False`)                              3 FAILED
       FAIL truncated / empty / notjson: this session's work is saved
       (store has no tape)

  3. the notice is built but never revealed
     (`if self._damaged:` -> `if False:`)                             5 FAILED
       all five "says so", and NO survival check -- the discrimination worth
       having, since keeping the bytes and telling the person are different
       promises and this suite fails them separately.

  4. a healthy store is treated as damaged
     (`if isinstance(data, dict):` -> `if False:`)                    5 FAILED
       FAIL wrongtypes / desync: repaired quietly, no damage notice
       FAIL a healthy store is read back, not quarantined
       FAIL ...and its variables survive
       FAIL ...and no notice is shown
     "a missing store is not a damaged one" stays green under this one: a file
     that was never there does not reach the isinstance branch at all. Recorded
     because I had it in this list before running it.

WHAT THIS SUITE DOES NOT COVER: whether the person can get their numbers back
OUT of a .damaged-<stamp> file. There is no import path for one, in this app or
any other -- recovery today means opening it in a text editor. That is a real
gap and it is a product decision, not a defect; it is written up in the day's
task file rather than quietly fixed here.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CALCULATOR_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

# sanitize_state is checked directly at the bottom of this file, so the module
# is imported here as well as inside the worker. It reads NB_HOME at import, so
# the directory has to exist first.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calc-damage-own-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)
from calculator import sanitize_state                         # noqa: E402

CYCLES = 3
MARK = "3.14159265"

# A real calculator's worth of work, in each of the shapes a store can arrive in
# when something else has been at it. The dict shapes are what sanitize_state is
# for; the array and the string are the ones no helper was being called for.
# `variables` FIRST so a truncated copy still contains the marker: the whole
# point of the truncated case is a file that holds real work and stops
# mid-write, and a fixture cut off before the number proves nothing.
WORK = {"variables": {"A": 3.14159265, "B": 2.71828, "C": 9.80665},
        "deg": False, "fix": 3, "ans": 1234.5,
        "tape": ["24*7", "1234.5/3"], "tape_results": ["168", "411.5"]}

UNREADABLE = {
    "array": json.dumps([WORK]),
    "string": json.dumps("A=3.14159265 B=2.71828 C=9.80665"),
    "truncated": json.dumps(WORK)[:60],   # cuts mid-value: valid work, invalid JSON
    "empty": "",
    "notjson": "A=3.14159265\nB=2.71828\nnot json at all",
}
# Parses AS a calculator, just with fields that need repairing. sanitize_state's
# job, NOT the quarantine's -- salvaging these must stay silent.
REPAIRABLE = {
    "wrongtypes": json.dumps(dict(WORK, fix="three", deg="yes",
                                  tape="notalist")),
    "desync": json.dumps(dict(WORK, tape=["1+1", "2+2", "3+3"],
                              tape_results=["2"])),
}

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


WORKER = r'''
import os, sys
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
# Per PROCESS, or nbapp.claim_single_instance() finds a live registration and
# os._exit(0)s this worker with status 0 -- a silent false pass.
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
import calculator


def pump():
    for _ in range(6):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


w = calculator.Calculator()
pump()
if os.environ.get("DO_WORK"):
    w.press(("7", "app", "7", "num"))
    w.press(("+", "app", "7", "num"))
    w.press(("7", "app", "7", "num"))
    w.press(("=", "eq", None, "eq"))
    pump()
print("REVEALED=%s" % bool(w.damage_rev.get_reveal_child()))
print("DAMAGED=%s" % bool(getattr(w, "_damaged", None)))
w.destroy()
pump()
print("RAN")
'''


def cycle(home, do_work=False):
    """One open-and-close, the way the Finder launches it and Esc closes it."""
    env = dict(os.environ, NB_HOME=home,
               DISPLAY=os.environ.get("DISPLAY", ":0"),
               PYTHONPATH=MODULE_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""))
    if do_work:
        env["DO_WORK"] = "1"
    r = subprocess.run([sys.executable, "-c", WORKER], capture_output=True,
                       text=True, timeout=180, env=env)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return None, "did not launch: %s" % (err[-1][:100] if err else "?")
    out = dict(l.split("=", 1) for l in r.stdout.split()
               if "=" in l and l.split("=")[0].isupper())
    return out, r.stdout


def holders(home):
    """Every file under `home` whose bytes still contain the user's number."""
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


def plant(root, tag, raw):
    home = os.path.join(root, tag)
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "calculator.json"), "w") as fh:
        fh.write(raw)
    return home


# A survival check on a fixture that never held the marker is vacuous and
# reports green. Fail loudly instead -- this suite lost two checks to exactly
# that before the fixture was reordered.
for _tag, _raw in UNREADABLE.items():
    if _tag != "empty" and MARK not in _raw:
        print("FAIL fixture %r does not contain the marker it is graded on"
              % _tag)
        sys.exit(2)

root = tempfile.mkdtemp(prefix="calc_damage_")

# ---------------------------------------------- an unreadable store SURVIVES
for tag, raw in UNREADABLE.items():
    home = plant(root, tag, raw)
    first, lost_on = None, None
    for n in range(1, CYCLES + 1):
        out, raw_out = cycle(home)
        if out is None:
            check("%s: opens at all" % tag, False, raw_out)
            break
        left = holders(home)
        if first is None:
            first = left
            check("%s: says so" % tag, out.get("REVEALED") == "True",
                  "notice not revealed (DAMAGED=%s)" % out.get("DAMAGED"))
        if not left and lost_on is None:
            lost_on = n
    else:
        # The empty file holds no bytes to find, so its survival is the
        # quarantine FILE existing, not the marker. Everything else must still
        # have the person's number somewhere after three cycles.
        if tag == "empty":
            q = [f for f in os.listdir(os.path.join(home, ".config", "notebook"))
                 if ".damaged-" in f]
            check("%s: the file is moved aside, not deleted" % tag, bool(q),
                  os.listdir(os.path.join(home, ".config", "notebook")))
        else:
            check("%s: bytes survive %d open+close cycles" % (tag, CYCLES),
                  lost_on is None,
                  "LOST on cycle %s (after #1 it was in %s)"
                  % (lost_on, ";".join(first or []) or "nowhere"))
            check("%s: and they are in a .damaged- file, not just the .bak" % tag,
                  any(".damaged-" in f for f in holders(home)),
                  holders(home))

# ------------------------------------- ...and the session's work is STILL saved
# The regression guard for the cure that cost more than the disease.
for tag in ("truncated", "empty", "notjson"):
    home = plant(root, "work-" + tag, UNREADABLE[tag])
    out, raw_out = cycle(home, do_work=True)
    store = os.path.join(home, ".config", "notebook", "calculator.json")
    try:
        with open(store) as fh:
            saved = json.load(fh)
    except Exception as exc:
        saved = {"__err__": str(exc)}
    check("%s: this session's work is saved" % tag,
          bool(saved.get("tape")),
          "store has no tape: %s" % json.dumps(saved)[:120])

# ------------------------------------------ a REPAIRABLE store is not a damaged one
for tag, raw in REPAIRABLE.items():
    home = plant(root, tag, raw)
    out, raw_out = cycle(home)
    if out is None:
        check("%s: opens at all" % tag, False, raw_out)
        continue
    check("%s: repaired quietly, no damage notice" % tag,
          out.get("REVEALED") == "False",
          "the notice fired for a store that only needed its fields fixed")

# ------------------------------------------------------- a HEALTHY store is left alone
home = plant(root, "healthy", json.dumps(WORK))
out, _ = cycle(home)
cfg = os.path.join(home, ".config", "notebook")
check("a healthy store is read back, not quarantined",
      not any(".damaged-" in f for f in os.listdir(cfg)), os.listdir(cfg))
check("...and its variables survive", MARK in open(
    os.path.join(cfg, "calculator.json")).read())
check("...and no notice is shown", out and out.get("REVEALED") == "False",
      out)

# --------------------------------------------------- a MISSING store is not damage
home = os.path.join(root, "missing")
os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
out, _ = cycle(home)
check("a missing store is not a damaged one",
      out and out.get("DAMAGED") == "False", out)

# ------------------------------------ what sanitize_state actually throws away
# Found by the mutation sweep: swaps inside the variable filter, the fix range
# and the window ordering all SURVIVED every suite, and the fingerprint triage
# confirmed all three CHANGE BEHAVIOUR. The store is the one thing here a person
# cannot inspect or repair by hand, so what it accepts has to be pinned.
_vars = {"A": 1.0,      # kept: a single capital holding a finite number
         "P": 7,        # kept: an int is a number
         "b": 2.0,      # dropped: not a capital
         "AA": 3.0,     # dropped: not a SINGLE letter
         "Z": float("nan"),   # dropped: not finite
         "M": True,     # dropped: a bool is not a number here
         "N": "5",      # dropped: a string is not a number
         "": 1.0, "1": 2.0}   # dropped: not letters at all
_kept = sorted(sanitize_state({"variables": _vars})["variables"])
check("only single capital letters holding finite numbers are kept as variables",
      _kept == ["A", "P"], _kept)

for _fix, _want in ((0, 0), (5, 5), (9, 9), (-1, None), (10, None),
                    (True, None), ("2", None), (None, None), (1.5, None)):
    _got = sanitize_state({"fix": _fix})["fix"]
    check("a stored fix of %r loads as %r" % (_fix, _want), _got == _want, _got)

# Both shapes, and the EQUAL one is the point: a `>=` written as `>` still
# rejects 5..-5 (5 > -5 is true) and quietly accepts 5..5, which is a window
# with no width that divides by zero on the next draw. The reversed case alone
# cannot tell those two guards apart -- measured, that mutation survived it.
for _name, _win in (
        ("reversed", {"xmin": 5., "xmax": -5., "ymin": 3., "ymax": -3.}),
        ("flat", {"xmin": 5., "xmax": 5., "ymin": 1., "ymax": 1.})):
    _w = sanitize_state({"window": dict(_win, xscl=1., yscl=1.)})["window"]
    check("a %s window is reset, not kept" % _name,
          (_w["xmin"], _w["xmax"], _w["ymin"], _w["ymax"])
          == (-10., 10., -10., 10.), _w)

shutil.rmtree(root, ignore_errors=True)
bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
