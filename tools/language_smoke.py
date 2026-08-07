#!/usr/bin/env python3
"""Open every screen of Language for real, in order, and fail on the first one
that raises.

This is the cheap gate that runs after every edit: construct_all proves the app
IMPORTS, and language_course_selftest proves a lesson can be finished, but
neither of them opens the tips page, the vocabulary list, the awards page, the
alphabet card, the skill card over the path or the out-of-hearts screen -- and
a screen nobody built is a screen that crashes the first time somebody taps it.

    DISPLAY=:0 python3 tools/language_smoke.py
"""
import os
import sys
import time
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if DE not in sys.path:
    sys.path.insert(0, DE)

HOME = tempfile.mkdtemp(prefix="language_smoke_")
os.environ["NB_HOME"] = HOME

import gi                                                    # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                # noqa: E402

import nbapp                                                 # noqa: E402
# claim_single_instance() calls os._exit(0) when it finds a live registration in
# the shared /tmp/nb-apps: no output, status 0, a silent false pass.
nbapp._APP_DIR = os.path.join(HOME, "nb-apps")
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import language                                              # noqa: E402

fails = []


def pump(n=6):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def step(name, fn):
    try:
        fn()
        pump()
    except Exception:
        fails.append((name, traceback.format_exc().strip().split("\n")[-1]))
        return False
    return True


def walk(w):
    yield w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            for x in walk(c):
                yield x


def main():
    w = language.Language()
    w.realize()
    # show_all, not just realize: a GtkStack refuses to switch to a child that
    # has never been made visible, so without this every set_visible_child_name
    # in the app is a silent no-op and the tests below all read whichever page
    # happened to be up. The real app is shown by nbapp.run().
    w.get_child().show_all()
    pump()
    if not w.courses:
        print("no courses loaded")
        return 2
    print("courses: %s" % ", ".join(c["name"] for c in w.courses))

    for c in w.courses:
        code = c["code"]
        step("%s open" % code, lambda co=c: w._open_course(co))
        nunits = len(c["units"])
        nskills = sum(len(u.get("skills", [])) for u in c["units"])
        print("  %-16s %2d units %3d skills  path %d nodes"
              % (c["name"], nunits, nskills, nskills + nunits))

        # every screen the course page can reach
        step("%s skill card" % code,
             lambda: w._show_skill_card(0, 0, c["units"][0]["skills"][0]))
        step("%s hide card" % code, w._hide_card)
        step("%s test card" % code, lambda: w._show_test_card(0))
        step("%s hide card 2" % code, w._hide_card)
        step("%s skill tips" % code, lambda: w._show_skill_tips(0, 0))
        step("%s unit tips" % code, lambda: w._show_unit_tips(0))
        step("%s alphabet" % code, w._show_alphabet)
        step("%s vocab" % code, w._show_vocab)
        step("%s awards" % code, w._show_awards)
        step("%s back" % code, w._back_to_course)
        step("%s locked toast" % code, lambda: w._tap_skill(
            nunits - 1, 0, c["units"][-1]["skills"][0], "locked"))
        step("%s no hearts card" % code, w._no_hearts_card)
        step("%s hide card 3" % code, w._hide_card)

        # tips exist and are readable on every skill
        missing = [(u["title"], s["name"])
                   for u in c["units"] for s in u.get("skills", [])
                   if not s.get("tips")]
        if missing:
            fails.append(("%s tips" % code,
                          "%d skills carry no tips, first %s"
                          % (len(missing), missing[0])))

        # a lesson at every crown level builds and is answerable in shape
        for level in range(language.CROWN_MAX):
            w.progress.setdefault("crowns", {})["%s:0:0" % code] = level
            L = w._build_lesson(0, 0)
            if not L:
                fails.append(("%s level %d" % (code, level), "no lesson built"))
                continue
            kinds = {}
            for ex in L["ex"]:
                kinds[ex["kind"]] = kinds.get(ex["kind"], 0) + 1
                if ex["kind"] in ("choose", "listen"):
                    if len(ex["options"]) < 2:
                        fails.append(("%s level %d" % (code, level),
                                      "a choice with %d options"
                                      % len(ex["options"])))
                    if ex["answer"] not in ex["options"]:
                        fails.append(("%s level %d" % (code, level),
                                      "answer %r not among options"
                                      % ex["answer"]))
                if ex["kind"] == "bank":
                    # MULTIPLICITY, not membership. A sentence that uses the
                    # same word twice ("Mi vidas la sunon kaj la lunon") needs
                    # two of that tile; a bank holding one is a sentence that
                    # cannot be built, and `tok in bank` says nothing about it.
                    want = {}
                    for tok in (ex.get("tokens") or ex["answer"].split()):
                        want[tok] = want.get(tok, 0) + 1
                    for tok, n in want.items():
                        if ex["bank"].count(tok) < n:
                            fails.append(("%s level %d" % (code, level),
                                          "bank has %d x %r, the sentence "
                                          "needs %d"
                                          % (ex["bank"].count(tok), tok, n)))
            print("     level %d: %s" % (level, ", ".join(
                "%s x%d" % (k, n) for k, n in sorted(kinds.items()))))
        w.progress.setdefault("crowns", {})["%s:0:0" % code] = 0

    # practice needs something learned first
    w._open_course(w.courses[0])
    code = w.courses[0]["code"]
    first = w.courses[0]["units"][0]["skills"][0]
    for it in (first.get("words") or [])[:6]:
        w.progress.setdefault("seen", []).append(
            "%s:%s" % (code, language._norm(it["t"])))
    step("practice", w._start_practice)
    step("test", lambda: w._start_test(0))

    # Esc leaves ONE level from every page, and closes only from the picker.
    # A wrong target here is how "Esc deletes my work" reports start.
    from gi.repository import Gdk as _Gdk

    class _Esc(object):
        keyval = _Gdk.KEY_Escape
        state = 0

    def esc_from(setup, want):
        setup()
        pump()
        w._on_key(w, _Esc())
        pump()
        got = w.stack.get_visible_child_name()
        if got != want:
            fails.append(("esc from %s" % want,
                          "landed on %r, expected %r" % (got, want)))

    c0 = w.courses[0]
    esc_from(lambda: (w._open_course(c0), w._show_vocab()), "course")
    esc_from(lambda: (w._open_course(c0), w._show_skill_card(
        0, 0, c0["units"][0]["skills"][0])), "course")   # card closes, page stays
    esc_from(lambda: w._open_course(c0), "home")

    # the dialogs, without a main loop to run them: build only
    step("home", w._show_home)
    step("menu items", lambda: w.menu_items("File"))

    # Nothing in the menu may navigate away from a LIVE lesson: every one of
    # those items replaces the page, and the run, its crown and its XP would go
    # with it on a click that gave no warning.
    w._open_course(w.courses[0])
    pump()
    w._lesson = language.Language._lesson_state([], kind="lesson")
    live = dict((lbl, act) for lbl, act in
                [i for i in w.menu_items("File") if isinstance(i, tuple)])
    for lbl in ("Courses", "Practice", "Vocabulary", "Awards"):
        if live.get(lbl) is not None:
            fails.append(("menu during lesson",
                          "%r is live and would discard the lesson" % lbl))
    w._lesson = None
    back = dict((lbl, act) for lbl, act in
                [i for i in w.menu_items("File") if isinstance(i, tuple)])
    for lbl in ("Practice", "Vocabulary", "Awards"):
        if back.get(lbl) is None:
            fails.append(("menu after lesson", "%r stayed greyed out" % lbl))
    step("hearts toggle", w._toggle_hearts)
    step("hearts toggle back", w._toggle_hearts)

    # progress store hardening: nothing here may raise, whatever the file says
    for junk in ({}, {"xp": "12", "crowns": "no", "seen": {"a": 1}},
                 {"hearts": "x", "strength": {"a": 5, "b": {"s": "3"}}},
                 {"stats": 7, "awards": [1, 2], "goal": 99, "tests": None},
                 {"xp": [1], "streak": {"a": 1}, "heart_time": "soon"}):
        try:
            p = language.Language.norm_progress(junk)
            assert isinstance(p["crowns"], dict)
            assert isinstance(p["seen"], list)
            assert 0 <= p["hearts"] <= language.HEARTS_MAX
            assert p["goal"] in language.GOALS
        except Exception as e:
            fails.append(("norm_progress %r" % junk, str(e)))

    print()
    if fails:
        for where, why in fails:
            print("FAIL %-24s %s" % (where, why))
        print("\n%d failures" % len(fails))
        return 1
    print("all screens open, %d courses" % len(w.courses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
