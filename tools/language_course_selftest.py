#!/usr/bin/env python3
"""Play EVERY skill of EVERY shipped course to a perfect lesson, through the real
window and the real handlers, and prove the crown lands.

WHAT THIS IS FOR: language.py does not ship lessons, it GENERATES them from
de/course_<code>.json. A course file that looks fine can still produce a lesson
nobody can finish -- a multiple choice whose correct option is not among the
options, a word bank missing a token of its own answer, a skill that yields no
exercises at all so tapping it does nothing. None of that shows up in a store
test or in construct_all; the only way to know is to sit the lesson.

So this drives the actual widgets: it finds the Check button and presses it,
types into the real Gtk.Entry, clicks the real choice buttons and word tiles,
and taps the real matching pairs. A perfect run must end with wrong == 0 and
one more crown on that skill -- if the generator can produce an unanswerable
question, this fails.

  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/language_course_selftest.py [--full] [code ...]

Default is one pass over every skill of every course (the first-lesson path,
which is intro-heavy). --full also replays one course to CROWN_MAX, which is a
different code path: with every term already in `seen` there are no teaching
cards and the drills are drawn from the whole skill.
"""
import os
import sys
import json
import time
import random
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if DE not in sys.path:
    sys.path.insert(0, DE)

# A throwaway home, set BEFORE the app modules read it. A suite was once caught
# writing into the developer's real home and deleting his ~/.Trash.
HOME = tempfile.mkdtemp(prefix="language_course_")
os.environ["NB_HOME"] = HOME

import gi                                                    # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib                          # noqa: E402

import nbapp                                                 # noqa: E402
# claim_single_instance() calls os._exit(0) when it finds a live registration in
# the shared /tmp/nb-apps: no output, status 0, a silent false pass.
nbapp._APP_DIR = os.path.join(HOME, "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import language                                              # noqa: E402

ADVANCE_MS = 40          # polls to wait out the 750ms correct-answer timer


def pump(rounds=8, secs=0.0):
    end = time.time() + secs
    while True:
        for _ in range(rounds):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        if time.time() >= end:
            return
        time.sleep(0.02)


def walk(w):
    yield w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            for x in walk(c):
                yield x


def find(root, cls):
    return [x for x in walk(root) if isinstance(x, cls)]


def classed(root, css):
    return [b for b in find(root, Gtk.Button)
            if css in b.get_style_context().list_classes()]


def button(root, label):
    for b in find(root, Gtk.Button):
        if b.get_label() == label:
            return b
        ch = b.get_child()
        if isinstance(ch, Gtk.Label) and ch.get_text() == label:
            return b
    return None


def answer_exercise(w, ex, fails, where):
    """Answer the showing exercise CORRECTLY through its own widgets. Returns
    False when the exercise cannot be answered at all -- which is the defect
    class this suite exists for."""
    h = w._lesson_holder
    kind = ex["kind"]
    if kind == "intro":
        b = button(h, "Continue")
        if b is None:
            fails.append(where + ("teaching card has no Continue",))
            return False
        b.clicked()
        return True

    if kind == "type":
        es = find(h, Gtk.Entry)
        if not es:
            fails.append(where + ("typing exercise has no entry box",))
            return False
        es[0].set_text(ex["answer"])
        pump()
        b = button(h, "Check")
        if b is None or not b.get_sensitive():
            fails.append(where + ("Check is dead with the answer typed",))
            return False
        b.clicked()
        return True

    if kind == "choose":
        hit = [b for b in classed(h, "choicebtn") if b.get_label() == ex["answer"]]
        if not hit:
            fails.append(where + ("correct option %r missing from %r"
                                  % (ex["answer"], ex["options"]),))
            return False
        # An unanswerable question: two options the grader would both have to
        # accept, only one of which it does. See language._synonyms.
        acc = {ex["answer"]} | set(ex.get("alts") or [])
        rivals = [o for o in ex["options"]
                  if language._norm(o) in {language._norm(a) for a in acc}]
        if len(rivals) > 1:
            fails.append(where + ("two accepted options offered: %r" % rivals,))
            return False
        hit[0].clicked()
        pump()
        b = button(h, "Check")
        if b is None or not b.get_sensitive():
            fails.append(where + ("Check is dead with an option picked",))
            return False
        b.clicked()
        return True

    if kind == "bank":
        for tok in ex["answer"].split():
            tile = [b for b in classed(h, "banktile") if b.get_label() == tok]
            if not tile:
                fails.append(where + ("word bank has no tile %r (bank=%r)"
                                      % (tok, ex["bank"]),))
                return False
            tile[0].clicked()
            pump()
        b = button(h, "Check")
        if b is None or not b.get_sensitive():
            fails.append(where + ("Check is dead with the sentence built",))
            return False
        b.clicked()
        return True

    if kind == "match":
        tiles = classed(h, "matchtile")
        cols = []
        for b in tiles:
            if b.get_parent() not in cols:
                cols.append(b.get_parent())
        if len(cols) < 2:
            fails.append(where + ("matching round has no two columns",))
            return False
        # Distinguish the columns by their CONTAINER, never by label text: a word
        # whose target and English spelling are identical (Spanish "no", French
        # "six", "train") otherwise matches a tile on the wrong side, and the
        # round can never be completed. That was this suite's own bug first.
        left = [b for b in tiles if b.get_parent() is cols[0]]
        right = [b for b in tiles if b.get_parent() is cols[1]]
        for pair in ex["pairs"]:
            lt = [b for b in left
                  if isinstance(b.get_child(), Gtk.Label)
                  and b.get_child().get_text().split("\n")[0] == pair[0]]
            rt = [b for b in right if b.get_label() == pair[1]]
            if not lt or not rt:
                fails.append(where + ("matching pair %r has no tiles" % (pair,),))
                return False
            lt[0].clicked()
            pump()
            rt[0].clicked()
            pump()
        return True

    fails.append(where + ("unknown exercise kind %r" % kind,))
    return False


def play(w, ui, si, skill, code, unit, fails, expect_crown):
    where = (code, unit, skill.get("name"))
    w._start_lesson(ui, si)
    pump()
    if w._lesson is None or not w._lesson["ex"]:
        fails.append(where + ("tapping the skill produced NO lesson",))
        return {}
    kinds = {}
    guard = 0
    while w.stack.get_visible_child_name() == "lesson" and guard < 400:
        guard += 1
        L = w._lesson
        if L["i"] >= len(L["ex"]):
            pump()
            break
        ex = L["ex"][L["i"]]
        kinds[ex["kind"]] = kinds.get(ex["kind"], 0) + 1
        before = L["i"]
        if not answer_exercise(w, ex, fails, where):
            return kinds
        for _ in range(ADVANCE_MS):
            pump(2)
            if (w._lesson["i"] != before
                    or w.stack.get_visible_child_name() != "lesson"):
                break
            GLib.main_context_default().iteration(False)
            time.sleep(0.03)
        if (w._lesson["i"] == before
                and w.stack.get_visible_child_name() == "lesson"):
            fails.append(where + (
                "stuck on a %s exercise (graded=%s wrong=%d)"
                % (ex["kind"], w._graded, w._lesson["wrong"]),))
            return kinds
    if w._lesson and w._lesson["wrong"]:
        fails.append(where + ("a PERFECT run was scored %d wrong"
                              % w._lesson["wrong"],))
    got = w._crowns(ui, si)
    if got != expect_crown:
        fails.append(where + ("crown is %d, expected %d" % (got, expect_crown),))
    done = button(w._lesson_holder, "Continue")
    if done is not None:
        done.clicked()
        pump()
    return kinds


class _Typed(object):
    """The smallest thing _check_type will read a typed answer out of."""

    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


def check_ambiguous(w, c, fails):
    """Every term a course records TWO meanings for must accept both.

    WHY THIS IS A SEPARATE, DETERMINISTIC CHECK: playing the lessons does find
    an unanswerable multiple choice, but only by luck -- the rival option has to
    be one of three distractors sampled out of a hundred-odd, so a full pass
    over French hits it a few percent of the time and passes the rest. The
    defect it exists for (French `fille` = girl AND daughter, Mandarin `shi` =
    yes AND to be, one of which was marked WRONG) does not deserve a coin toss.
    So ask the data directly which terms are ambiguous and grade every reading."""
    w._open_course(c)
    pump()
    pool = w._course_words()
    w._syn_t, w._syn_e = w._synonyms(pool)
    ambiguous = {k: v for k, v in w._syn_t.items() if len(set(v)) > 1}
    for term, meanings in sorted(ambiguous.items()):
        meanings = sorted(set(meanings))
        where = (c["code"], "ambiguous", term)
        for want in meanings:
            it = {"t": term, "e": want, "ipa": "", "phrase": False}
            ex = w._make_exercise("translate_to_en", it, [], pool)
            for other in meanings:
                w._lesson = {"ui": 0, "si": 0, "ex": [ex], "i": 0, "wrong": 0,
                             "new_keys": []}
                w._graded = False
                w._check_btn = None
                w._result_lbl = Gtk.Label()
                w._check_type(_Typed(other), ex)
                if w._lesson["wrong"]:
                    fails.append(where + (
                        "typing %r for %r (asked as %r) was marked WRONG"
                        % (other, term, want),))
            # A nonsense answer must still be wrong, or "accepts everything"
            # would pass this check.
            w._lesson = {"ui": 0, "si": 0, "ex": [ex], "i": 0, "wrong": 0,
                         "new_keys": []}
            w._graded = False
            w._result_lbl = Gtk.Label()
            w._check_type(_Typed("qqzzxx"), ex)
            if not w._lesson["wrong"]:
                fails.append(where + ("nonsense was accepted as %r" % want,))
        # and no multiple choice may ever offer two accepted readings
        acc = {language._norm(m) for m in meanings}
        for _ in range(300):
            it = {"t": term, "e": meanings[0], "ipa": "", "phrase": False}
            ex = w._make_exercise("choose", it, [], pool)
            rivals = [o for o in ex["options"] if language._norm(o) in acc]
            if len(rivals) > 1:
                fails.append(where + (
                    "multiple choice offered two accepted answers: %r" % rivals,))
                break
    w._lesson = None
    return len(ambiguous)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    full = "--full" in sys.argv
    random.seed(20260730)
    w = language.Language()
    w.get_child().show_all()
    pump()
    courses = [c for c in w.courses if not args or c.get("code") in args]
    if not courses:
        print("no courses found in %s" % DE)
        return 1
    fails = []
    kinds = {}
    skills = 0
    ambig = 0
    for c in courses:
        ambig += check_ambiguous(w, c, fails)
    for c in courses:
        w._open_course(c)
        pump()
        for ui, unit in enumerate(c.get("units", [])):
            for si, skill in enumerate(unit.get("skills", [])):
                skills += 1
                for k, v in play(w, ui, si, skill, c["code"],
                                 unit.get("title"), fails, 1).items():
                    kinds[k] = kinds.get(k, 0) + v
        print("%-4s %-16s %d units, %d skills played"
              % (c["code"], c.get("name"), len(c.get("units", [])),
                 sum(len(u.get("skills", [])) for u in c.get("units", []))))
    if full:
        c = courses[0]
        w._open_course(c)
        pump()
        for rnd in range(2, 7):        # crowns 2..5, then one beyond the cap
            for ui, unit in enumerate(c.get("units", [])):
                for si, skill in enumerate(unit.get("skills", [])):
                    for k, v in play(w, ui, si, skill, c["code"],
                                     unit.get("title"), fails,
                                     min(5, rnd)).items():
                        kinds[k] = kinds.get(k, 0) + v
        print("%-4s replayed to %d crowns" % (c["code"], language.CROWN_MAX))

    # The progress those lessons earned has to be on disk after the close.
    w.destroy()
    pump()
    store = os.path.join(HOME, ".config", "notebook", "language.json")
    try:
        with open(store, encoding="utf-8") as fh:
            saved = json.load(fh)
    except Exception as exc:
        fails.append(("-", "-", "progress store", "not readable: %r" % exc))
        saved = {}
    if len(saved.get("crowns") or {}) < skills:
        fails.append(("-", "-", "progress store",
                      "%d crowns saved, %d skills played"
                      % (len(saved.get("crowns") or {}), skills)))
    if not saved.get("xp"):
        fails.append(("-", "-", "progress store", "no XP saved"))

    print("")
    print("%d skills, %d exercises answered %s"
          % (skills, sum(kinds.values()),
             ", ".join("%s=%d" % kv for kv in sorted(kinds.items()))))
    print("%d term(s) with more than one recorded meaning, all readings graded"
          % ambig)
    print("saved: %d crowns, %s XP, %d terms seen"
          % (len(saved.get("crowns") or {}), saved.get("xp"),
             len(saved.get("seen") or [])))
    if fails:
        print("")
        for f in fails:
            print("FAIL %-4s %-14s %-14s %s"
                  % (f[0], str(f[1])[:14], str(f[2])[:14], f[3]))
        print("RESULT: %d PROBLEM(S)" % len(fails))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
