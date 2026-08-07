#!/usr/bin/env python3
"""
The crown rule is stated where it can be acted on.

A crown needs a lesson with NO mistakes. That rule is real and was never
written down anywhere in the app: a lesson finished at 9 of 10 showed "Lesson
complete", a score, and no crown — so a learner could sit the same skill again
and again, doing well every time, and never discover why the crown would not
come (ROADMAP #39).

This drives the real end-of-lesson screen and reads the labels off it. Both
directions are asserted, because a line that always appears is as useless as one
that never does:

  IMPERFECT   9 of 10 -> no crown, and the rule is on screen
  PERFECT     10 of 10 -> a crown, and the rule is NOT on screen

and the two cases where it must stay quiet: a maxed-out skill (no crown left to
earn, so the rule is noise) and practice (never crowned at all).

Run:
    tools/guestrun.sh python3 tools/language_crown_rule_selftest.py
    tools/guestrun.sh python3 tools/language_crown_rule_selftest.py --de DIR
"""
import os
import sys
import time
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-crown-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

import nbapp  # noqa: E402
nbapp._APP_DIR = os.path.join(_HOME, "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import language  # noqa: E402

RULE = "no mistakes"
FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump(secs=0.0):
    end = time.time() + secs
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if time.time() >= end:
            return
        time.sleep(0.02)


def rule_line(said):
    """The crown-rule label if it is on screen, else ''. Reported instead of a
    truncated dump: the first version printed the first 130 characters of 118
    labels, which cut the line off and made a correct pass look vacuous."""
    return next((s for s in said if RULE in s), "")


def labels(w):
    """Every label currently on the reading surface."""
    out = []

    def walk(x):
        if isinstance(x, Gtk.Label):
            out.append(x.get_text() or "")
        if isinstance(x, Gtk.Container):
            for c in x.get_children():
                walk(c)
    walk(w)
    return out


def finish(w, kind="lesson", wrong=0, ui=0, si=0, graded=10):
    """Run the real end-of-lesson screen for a synthetic result."""
    # Built by the app's OWN factory, then adjusted. Hand-rolling the dict got
    # it wrong twice (a None exercise, then a missing "missed" key) — and a
    # test that invents its own version of a structure will drift away from the
    # real one silently.
    ex = [{"kind": "choose", "term": None} for _ in range(graded)]
    L = language.Language._lesson_state(ex, ui=ui, si=si, kind=kind)
    L["i"] = graded
    L["answered"] = graded
    L["wrong"] = wrong
    L["start"] = time.time() - 30
    w._lesson = L
    w._lesson_complete()
    pump(0.2)
    return labels(w)


def main():
    w = language.Language()
    pump(0.3)
    if not w.courses:
        check("a course is available to test against", False)
        return 1
    check("a course is available to test against", True)
    w._open_course(w.courses[0])
    pump(0.2)

    # ---- 9 of 10: no crown, and the rule must be visible -------------
    before = w._crowns(0, 0)
    said = finish(w, wrong=1)
    joined = " | ".join(said)
    no_crown = check("an imperfect lesson earns no crown",
                     w._crowns(0, 0) == before,
                     "crowns %d -> %d" % (before, w._crowns(0, 0)))
    check("...and the screen says what a crown needs",
          bool(rule_line(said)), "line: %r" % rule_line(said))

    # ---- 10 of 10: a crown, and the rule must NOT be there -----------
    before = w._crowns(0, 0)
    said = finish(w, wrong=0)
    got_crown = check("a perfect lesson earns the crown",
                      w._crowns(0, 0) == before + 1,
                      "crowns %d -> %d" % (before, w._crowns(0, 0)))
    if got_crown:
        check("...and the rule is not repeated once it has been met",
              not any(RULE in s for s in said),
              "unexpected line: %r" % rule_line(said))
    else:
        not_reached("no crown was awarded",
                    "...and the rule is not repeated once it has been met")

    # ---- a maxed skill has no crown left to earn ---------------------
    for _ in range(language.CROWN_MAX + 2):
        w._add_crown(0, 0)
    maxed = check("the skill can be filled to CROWN_MAX",
                  w._crowns(0, 0) >= language.CROWN_MAX,
                  "crowns=%d" % w._crowns(0, 0))
    if maxed:
        said = finish(w, wrong=1)
        check("a finished skill is not told how to earn a crown it has",
              not any(RULE in s for s in said),
              "unexpected line: %r" % rule_line(said))
    else:
        not_reached("could not fill the skill",
                    "a finished skill is not told how to earn a crown it has")

    # ---- practice is never crowned, so the rule is noise there -------
    said = finish(w, kind="practice", wrong=1, si=1)
    check("practice is not told about crowns either",
          not any(RULE in s for s in said),
          "unexpected line: %r" % rule_line(said))

    try:
        w.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
