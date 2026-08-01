#!/usr/bin/env python3
"""
text_stress_selftest — do Journal, Novel, Tasks and Cookbook still fit a
1024x740 panel when the TEXT in them is hostile?

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
    python3 tools/text_stress_selftest.py

minsize_sweep measures every app as it ships: empty, in English, with no user
data in it. That is the easy case, and all four of these apps passed it while
being one long string away from unusable. The strings that broke them are not
exotic:

  * journal   a long `meta` line. It is app-generated and short in normal use,
              but the loader deliberately survives a hand-edited or foreign
              journal.json and validates TYPES, not LENGTHS. Un-ellipsized, a
              ~500-character value made the app's minimum width 3208px, so the
              sidebar and two thirds of the page sat off a 1024px screen.
  * novel     a long manuscript title. Its WIDTH was capped and its HEIGHT was
              not, so it wrapped downward, took its full natural height (packed
              expand=False) and starved the chapter list to a ~26px sliver with
              "New Chapter" pushed off the bottom. Minimum height 915px.
  * tasks     ONE long unbroken word in an event title. A wrapping label's
              minimum width is its widest WORD and WrapMode.WORD will not break
              inside one: 34 characters — ordinary in German, Dutch or Finnish,
              and in any filename-style title — measured 1072px.
  * cookbook  a long category name. It is free text the cook types, and it is
              rendered twice: on its chip and, upper-cased and letter-spaced,
              as the recipe kicker. 1150px with Settings > Large text on,
              1205px in Greek.

Each case is measured in ITS OWN PROCESS. GTK CSS providers are added to the
SCREEN, not to a widget, so an app measured after another inherits its
stylesheet and the numbers drift by 10-20px (the same reason minsize_sweep
forks). NB_HOME is a throwaway directory seeded with the stress document, so
this never touches a real profile.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

W, H = 1024, 740

# Long enough to be pathological, short enough to be believable. The German
# word is the real one that used to headline this class of bug report.
LONG_PROSE = ("Written at the kitchen table while the rain kept on going and "
              "the kettle boiled twice over, somewhere north of the river, in "
              "a borrowed coat and a bad mood, with the radio murmuring. ")
LONG_WORD = "Rindfleischetikettierungsueberwachungsaufgabenuebertragungsgesetz"
LONG_CAT = "Weeknight Dinners That Take Under Half An Hour And Feed Four"


def seed(app, cfg):
    """Write the stress document for `app` into config dir `cfg`."""
    if app == "journal":
        json.dump({"entries": [{
            "day": "14", "wd": "Tuesday", "month_label": "July 2026",
            "date": "Tuesday 14 July 2026", "meta": (LONG_PROSE * 4)[:500],
            "title": "Rain again", "preview": "Rain again all day",
            "text": "Rain again all day.", "tags": []}], "active": 0},
            open(os.path.join(cfg, "journal.json"), "w"))
    elif app == "novel":
        json.dump({"title": (LONG_PROSE * 4)[:500],
                   "parts": [{"name": ""}],
                   "chapters": [{"title": "Chapter %d" % i, "body": ""}
                                for i in range(1, 9)],
                   "active": 0},
                  open(os.path.join(cfg, "novel.json"), "w"))
    elif app == "tasks":
        # The rail reads the CALENDAR app's shared store, not a private one.
        json.dump([{"date": time.strftime("%Y-%m-%d"), "start": 9.5,
                    "end": 10.5, "title": LONG_WORD[:40], "cal": "",
                    "where": LONG_WORD[:40]}],
                  open(os.path.join(cfg, "calendar.json"), "w"))
    elif app == "cookbook":
        json.dump({"recipes": [{"title": "Soup", "cat": LONG_CAT, "desc": "",
                                "ing": "water", "steps": "boil"}],
                   "cats": [LONG_CAT], "sel": 0},
                  open(os.path.join(cfg, "cookbook.json"), "w"))
    # Large text is an accessibility setting, not an edge case: it is the
    # single biggest multiplier on every measurement in the OS.
    if os.environ.get("STRESS_LARGE_TEXT"):
        json.dump({"large_text": True},
                  open(os.path.join(cfg, "settings.json"), "w"))


def measure(app):
    """Build `app` against the seeded NB_HOME and return its (min_w, min_h)."""
    import importlib
    import inspect
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import uishot
    import dialogshot
    import nbapp

    uishot.load_theme()
    nbapp.screen_size = lambda: (W, H)
    mod = importlib.import_module(app)
    dialogshot.install_app_css(mod)
    cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            cls = c
            break
    win = cls()
    child = win.get_child()
    win.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(W, H)
    off.add(child)
    off.show_all()
    for _ in range(60):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    return child.get_preferred_width()[0], \
        child.get_preferred_height_for_width(W)[0]


CASES = [
    ("journal", "a 500-character meta line", {}),
    ("novel", "a 500-character manuscript title", {}),
    ("tasks", "a 40-character unbroken event title", {}),
    ("cookbook", "a 59-character category name", {}),
    ("cookbook", "the same, with Large text on", {"STRESS_LARGE_TEXT": "1"}),
    ("cookbook", "the same, in Greek with Large text on",
     {"STRESS_LARGE_TEXT": "1", "NB_LANG": "el"}),
    ("journal", "the same, in Greek", {"NB_LANG": "el"}),
    ("tasks", "the same, in Greek", {"NB_LANG": "el"}),
]


def main():
    ok = bad = 0
    for app, what, env in CASES:
        child_env = dict(os.environ)
        child_env.update(env)
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--one", app],
            capture_output=True, text=True, timeout=180, env=child_env)
        line = [ln for ln in (proc.stdout or "").splitlines()
                if ln.startswith("{")]
        if not line:
            detail = ((proc.stderr or "").strip().splitlines()
                      or ["no output"])[-1]
            print("FAIL %-9s %-38s ERROR %s" % (app, what, detail[:60]))
            bad += 1
            continue
        d = json.loads(line[-1])
        over = d["w"] > W or d["h"] > H
        print("%s %-9s %-38s %4d x %-4d" %
              ("FAIL" if over else "PASS", app, what, d["w"], d["h"]))
        bad += bool(over)
        ok += not over
    print("\n%d checks, %d passed, %d FAILED" % (ok + bad, ok, bad))
    print("RESULT: " + ("ALL PASS" if not bad else "FAILURES"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(REPO, "tools"))
    sys.path.insert(0, DE)
    if len(sys.argv) > 2 and sys.argv[1] == "--one":
        app = sys.argv[2]
        # NB_HOME must be set BEFORE the app module is imported: every app
        # resolves its config path at import time, so seeding afterwards would
        # measure an empty profile and pass no matter what.
        home = tempfile.mkdtemp(prefix="txstress-")
        cfg = os.path.join(home, ".config", "notebook")
        os.makedirs(cfg)
        os.environ["NB_HOME"] = home
        try:
            seed(app, cfg)
            w, h = measure(app)
            print(json.dumps({"w": w, "h": h}))
        except Exception as exc:                                # noqa: BLE001
            print("ERROR %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
    raise SystemExit(main())
