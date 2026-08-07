#!/usr/bin/env python3
"""Render every screen of Language to a PNG, under the guest theme and fonts.

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
        python3 tools/language_shots.py OUTDIR [WxH ...]

Screens: the course picker, the path, a unit's tips,
the vocabulary list, awards, the alphabet, the skill card over the path, and one
of every exercise type plus both end screens.

Each shot is driven through the app's OWN methods, so what lands in the PNG is
what a learner gets, not a mock-up of it. The default sizes are the small-panel
budget (1024x740) -- anything that comes out bigger than the size asked for is
content a real laptop cannot reach.

To see the path BELOW the fold (the later units, their colours, the serpentine
continuing across a unit boundary) ask for a tall size instead of trying to
scroll: `python3 tools/language_shots.py OUTDIR 1024x2100`. Setting the
scroller's adjustment from here does not survive -- shot_window re-allocates
the widget after the hook runs and the value clamps back to the top, which
produced four PNGs of the top of the page all labelled "scrolled".

Two environment variables matter:

    NB_SHOT_COURSE=zh    which course to photograph. Mandarin and
                         Serbo-Croatian carry scripts and IPA the Latin
                         courses never reach, so "it looks fine in Spanish"
                         is not a render check.
    NB_LANG=ja           which UI language to photograph it IN. This is the
                         only way to catch a catalog key whose existing sense
                         is wrong for its new use -- "Words" came back as the
                         text editor's CHARACTER COUNT in ja/ko/zh, which is
                         invisible in English and invisible in the catalogs.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import appshot  # noqa: E402
import uishot  # noqa: E402


# Which course the shots are taken in. Mandarin and Serbo-Croatian exercise
# scripts and IPA the Latin courses never reach, so the harness has to be able
# to photograph them.
COURSE = os.environ.get("NB_SHOT_COURSE", "es")


def seed(app, code=None, crowns=((0, 0, 5), (0, 1, 3), (0, 2, 1)), xp=340,
         streak=6, learned=40):
    """A learner mid-course, so the path is not a wall of grey circles and the
    stat chips have numbers in them. An empty store renders a screen that is
    honest about a brand-new install and useless for judging the design."""
    code = code or COURSE
    course = next((c for c in app.courses if c["code"] == code), app.courses[0])
    p = app.progress
    p["xp"] = xp
    p["day_xp"] = 25
    p["streak"] = streak
    p["streak_day"] = __import__("time").strftime("%Y-%m-%d")
    p["hearts"] = 3
    p["heart_time"] = __import__("time").time() - 300
    p["stats"] = {"lessons": 34, "perfect": 17, "best_streak": streak}
    for ui, si, n in crowns:
        p.setdefault("crowns", {})["%s:%d:%d" % (course["code"], ui, si)] = n
    seen, strength = p.setdefault("seen", []), p.setdefault("strength", {})
    import language
    n = 0
    for u in course["units"]:
        for s in u.get("skills", []):
            for it in (s.get("words") or []) + (s.get("phrases") or []):
                if n >= learned:
                    break
                k = "%s:%s" % (course["code"], language._norm(it["t"]))
                seen.append(k)
                strength[k] = {"s": n % 5, "t": __import__("time").time()}
                n += 1
    return course


SHOTS = []


def shot(name):
    def deco(fn):
        SHOTS.append((name, fn))
        return fn
    return deco


@shot("01-home")
def _home(app):
    seed(app)
    app._show_home()


@shot("02-path-top")
def _path(app):
    app._open_course(seed(app))


@shot("04-skill-card")
def _card(app):
    c = seed(app)
    app._open_course(c)
    app._show_skill_card(0, 1, c["units"][0]["skills"][1])


@shot("05-unit-test-card")
def _testcard(app):
    c = seed(app)
    app._open_course(c)
    app._show_test_card(0)


@shot("06-tips")
def _tips(app):
    app._open_course(seed(app))
    app._show_skill_tips(0, 0)


@shot("07-unit-tips")
def _utips(app):
    app._open_course(seed(app))
    app._show_unit_tips(0)


@shot("08-vocabulary")
def _vocab(app):
    app._open_course(seed(app))
    app._show_vocab()


@shot("09-awards")
def _awards(app):
    seed(app)
    app._show_awards()


@shot("10-alphabet")
def _alpha(app):
    app._open_course(seed(app))
    app._show_alphabet()


def _exercise(app, kind, level=2):
    """Put one exercise of a given kind on screen, built from real course data."""
    import language
    c = seed(app)
    app._open_course(c)
    skill = c["units"][0]["skills"][0]
    words, phrases = app._skill_items(skill)
    it = (phrases if kind in ("bank", "blank") else words)[0]
    if kind == "intro":
        ex = app._make_exercise("intro", it, words)
    elif kind == "match":
        ex = app._make_exercise("match", None, words)
    else:
        ex = app._make_exercise(kind, it, words, phrases)
    app._lesson = language.Language._lesson_state([ex], title=skill["name"])
    app._lesson["combo"] = 4
    app._render_exercise()
    app.stack.set_visible_child_name("lesson")


for _k in ("intro", "match", "choose", "select", "listen", "bank", "blank",
           "translate_to_en", "translate_to_t"):
    def _mk(k):
        @shot("2%d-ex-%s" % (("intro match choose select listen bank blank "
                              "translate_to_en translate_to_t").split().index(k),
                             k))
        def _f(app, _k=k):
            _exercise(app, _k)
        return _f
    _mk(_k)


@shot("30-graded-wrong")
def _wrong(app):
    _exercise(app, "choose")
    ex = app._lesson["ex"][0]
    app._choice_result["picked"] = "—"
    app._grade(False, ex)


@shot("31-lesson-complete")
def _done(app):
    import language
    c = seed(app)
    app._open_course(c)
    skill = c["units"][0]["skills"][0]
    words, _p = app._skill_items(skill)
    ex = [app._make_exercise("choose", w, words) for w in words[:6]]
    app._lesson = language.Language._lesson_state(ex, ui=0, si=0, kind="lesson",
                                                  title=skill["name"])
    app._lesson["i"] = len(ex)
    app._lesson["best_combo"] = 6
    app._lesson["missed"] = [(words[0]["t"], words[0]["e"])]
    app._lesson["wrong"] = 1
    app._lesson["start"] = __import__("time").time() - 96
    app.stack.set_visible_child_name("lesson")
    app._lesson_complete()


@shot("32-out-of-hearts")
def _hearts(app):
    c = seed(app)
    app._open_course(c)
    app.progress["hearts"] = 0
    app.progress["heart_time"] = __import__("time").time() - 60
    app.stack.set_visible_child_name("lesson")
    app._lesson = None
    app._out_of_hearts()


@shot("33-fresh-install")
def _fresh(app):
    app.progress = type(app).norm_progress({})
    app._show_home()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    outdir = sys.argv[1]
    sizes = [tuple(int(x) for x in s.split("x"))
             for s in (sys.argv[2:] or ["1024x740"])]
    os.makedirs(outdir, exist_ok=True)
    os.environ.setdefault("NB_HOME", os.path.join(outdir, "nbhome"))
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    uishot.load_theme()
    bad = 0
    for (w, h) in sizes:
        for name, fn in SHOTS:
            path = os.path.join(outdir, "%s_%dx%d.png" % (name, w, h))
            held = {"app": None}

            def setup(app, _held=held):
                _held["app"] = app

            def build(_win, _fn=fn, _held=held):
                # EVERY shot builds its screen HERE, after the window has been
                # shown -- not in setup. A GtkStack refuses to switch to a child
                # that has never been made visible, so a page assembled before
                # show_all is simply not the page that gets rendered: the first
                # run of this harness photographed the course path four times
                # and called them the lesson-end screens.
                def settle():
                    for _ in range(6):
                        while Gtk.events_pending():
                            Gtk.main_iteration_do(False)
                late = _fn(_held["app"])
                settle()
                if callable(late):
                    # Twice, with a settle between: a scroll adjustment set
                    # before the scroller has been allocated is clamped to
                    # zero, and the shot silently comes out at the top of the
                    # page again.
                    late()
                    settle()
                    late()
                    settle()
            try:
                # uishot.shot_window hands after_show the window it just
                # realised; the per-shot hooks do not want it.
                gw, gh = appshot.render(
                    "language", "Language", w, h, path, setup=setup,
                    after_show=build)
            except Exception as e:
                print("ERR  %-22s %s" % (name, repr(e)[:100]))
                bad += 1
                continue
            over = "" if (gw <= w and gh <= h) else \
                "   <<< OVERFLOWS by %dx%d" % (max(0, gw - w), max(0, gh - h))
            if over:
                bad += 1
            print("ok   %-22s %dx%d -> %dx%d%s" % (name, w, h, gw, gh, over))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
