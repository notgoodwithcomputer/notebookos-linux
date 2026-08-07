#!/usr/bin/env python3
"""
minsize_sweep — does every app actually FIT the smallest laptop panel?

The worst class of bug this OS has shipped is content that is simply
unreachable on real hardware: an app whose widget tree demands more room than
the screen has gets squeezed or clipped, and the part that falls off is often
the part you need (a Save button, the bottom of a form). It only shows on a
1024x740 or 1366x768 panel, never on the 1920 the audits used.

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
    python3 tools/minsize_sweep.py [WIDTHxHEIGHT ...]

ONE APP PER PROCESS. GTK CSS providers are added to the SCREEN, not to a
widget, so every app measured in this process leaves its stylesheet behind for
the ones after it. Measuring the whole suite in a single process therefore
inflated results by 10-20px and, worse, made a result depend on alphabetical
position — the same app measured differently depending on who ran before it.
Each app is now measured in its own subprocess (this file re-invoked with
--one), the same reason config_resilience_selftest forks.

MEASURE HEIGHT-FOR-WIDTH, NOT HEIGHT. `get_preferred_height()` asks a widget
how tall it is with NO width to go on, and GTK answers for its MINIMUM width —
so a wrapping label pinned narrow (packages.py's `max_width_chars(1)`, which is
deliberate and correct) reports the height of its whole sentence stacked one
word per line. That made Packages look like it needed 1388px of height when the
real layout gives it 548, and "fixing" the app made it slightly WORSE. Ask
`get_preferred_height_for_width(W)` — the question GtkWindow itself asks — or
this tool invents overflows that do not exist.

TIGHT IS REPORTED, NOT JUST OVER. An app that fits with 4px to spare has not
passed, it has been lucky: one more toolbar button, one longer translation, one
font change and it overflows with no warning. Anything inside TIGHT_PX of the
budget is called out so it can be given room before it becomes a bug report.

The app list is derived from the desktop's own launch table, so a newly added
app is swept automatically.
"""
import importlib
import inspect
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, DE)

# How close to the budget counts as living dangerously. nbapp's own sizing note
# aims to keep ~20px of width in hand; 40 gives a little warning before that.
TIGHT_PX = 40

# THE SWEEP USED TO MEASURE ONE LANGUAGE — the developer's. That is the wrong
# one: this file exists to catch content that does not fit a 1024px panel, its
# own TIGHT note says the risk is "one longer translation", and the OS ships
# seventeen. English is close to the SHORTEST of them. Measured on academics,
# whose sidebar width is set by its three segmented-control labels:
#
#     en/de/es/fr/it/nl/tr/eo/hi/ja/ko/yi/zh  1008     ru  1024
#     pt 1011   sr 1016   el 1017   pl 1021          <- the budget IS 1024
#
# So the app a plain run called "16px to spare" has ZERO to spare in Russian,
# and the gate said ALL FIT. Re-measuring every app in every language costs
# 17x, so only the apps that come out near the budget are re-measured, and only
# in the languages measured to be width-heavy. An app with 300px of slack in
# English is not one translation from overflowing; an app with 16px is.
RISK_LANGS = ("ru", "pl", "el", "sr", "pt", "de")
RETEST_PX = 120


def measure_one(name, W, H):
    """Measure one app in THIS process. Only ever called in a --one child, so
    the CSS it leaves on the screen dies with the process.

    THERE IS NO `view` ARGUMENT, and one was written and removed on 2026-08-06
    rather than shipped: a Gtk.Stack is hhomogeneous by DEFAULT, so it reports
    the MAXIMUM width over all of its pages whatever page happens to be visible.
    Measured on academics — pages notes/schedule/homework want 788/271/564 and
    the stack answers 788 for every one of them. Switching pages before
    measuring therefore cannot change a single number here, and a parameter that
    looks like it broadens coverage while doing nothing is worse than no
    parameter. Width is already covered for every tab. (The same is NOT true of
    ellipsis_sweep, which inspects MAPPED labels — only the visible page's
    labels are mapped, so that tool really does see one page.)"""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import uishot
    import dialogshot
    import nbapp

    os.environ.setdefault("NB_HOME", "/tmp/nbhome-minsize")
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    uishot.load_theme()
    # Apps that size themselves from the screen (music's fixed columns,
    # a reading column) must believe they are on THIS panel, or they
    # lay out for the developer's monitor and the sweep proves nothing.
    nbapp.screen_size = lambda: (W, H)

    mod = importlib.import_module(name)
    dialogshot.install_app_css(mod)
    cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            cls = c
            break
    if cls is None:
        return {"error": "no Gtk.Window subclass"}
    app = cls()
    child = app.get_child()
    app.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(W, H)
    off.add(child)
    off.show_all()
    for _ in range(60):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    out = {"w": child.get_preferred_width()[0],
           "h": child.get_preferred_height_for_width(W)[0]}
    off.destroy()
    return out


def elastic_floor(name, W, H, lang, mw):
    """The true width floor of an app whose alloc handler grows a column.

    Academics and Journal size a content column FROM the allocation
    (`_on_canvas_alloc` / `set_size_request(w, -1)` at journal.py:705), so
    the minimum this sweep reads after the layout pumps TRACKS the probe
    width — academics read as "0px to spare in Russian" when its real floor
    was ~230px lower (HANDOFF 2026-08-07, both app sessions independently).
    A grown request is not a need: the app would lay out happily narrower.

    The floor is found without any app-side declaration by shrinking the
    probe: at each step the reported minimum either FOLLOWS the window down
    (still elastic, keep going) or STICKS (the grow hit its clamp — that is
    the real minimum). A rigid app answers the first narrower probe with the
    same number and exits immediately, so nothing changes for the other 28.
    Returns (floor_w, was_elastic)."""
    floor_w = mw
    for probe in (W - 160, W - 320, W - 480, 560):
        if probe < 560 or probe >= floor_w:
            break
        got, _err = measure(name, probe, H, lang)
        if got is None:
            break
        if got[0] >= floor_w - 24:      # stopped tracking: found the clamp
            return got[0], got[0] < mw - 24
        floor_w = got[0]
    return floor_w, floor_w < mw - 24


def measure(name, W, H, lang=None):
    """Run one app in its own process. Returns (w, h) or (None, reason)."""
    env = dict(os.environ)
    if lang:
        env["NB_LANG"] = lang
        # Its own home per language: nbi18n persists the active language, and a
        # measurement must not inherit the one before it.
        env["NB_HOME"] = "/tmp/nbhome-minsize-%s" % lang
    try:
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--one",
             name, str(W), str(H)],
            capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMED OUT"
    data = None
    for ln in reversed((r.stdout or "").strip().splitlines()):
        if ln.startswith("{"):
            try:
                data = json.loads(ln)
            except ValueError:
                data = None
            break
    if data is None or "error" in (data or {}):
        detail = (data or {}).get("error") if data else \
            ((r.stderr or "").strip().splitlines() or ["no output"])[-1]
        return None, str(detail)[:70]
    return (data["w"], data["h"]), None


def main():
    # 722 = 768 − the 46px panel shell.py strut-reserves. The budget said 740
    # for weeks because nbapp's note claimed a 28px panel; Video shipped at 725
    # believing it. docs/PAPER-PHYSICS.md §E3.6 is the corrected derivation.
    sizes = [tuple(int(v) for v in s.split("x")) for s in sys.argv[1:]] or \
        [(1024, 722), (1366, 722)]

    import finder as _finder
    apps = sorted(set(_finder.APP_MODULES.values()) | {"finder"})

    failures, tight = [], []
    for (W, H) in sizes:
        print("\n=== %dx%d ===" % (W, H))
        for name in apps:
            got, err = measure(name, W, H)
            if got is None:
                print("  %-12s ERROR %s" % (name, err))
                continue
            mw, mh = got
            # Anything close to the budget in the shipped language is re-measured
            # in the width-heavy ones, and the WORST result is what counts —
            # the machine has to hold the app in whichever language it is set to.
            worst_lang = None
            if W - mw <= RETEST_PX or H - mh <= RETEST_PX:
                for lang in RISK_LANGS:
                    alt, _e = measure(name, W, H, lang)
                    if alt is None:
                        continue
                    if alt[0] > mw or alt[1] > mh:
                        if alt[0] > mw:
                            worst_lang = lang
                        mw, mh = max(mw, alt[0]), max(mh, alt[1])
            if worst_lang:
                name_shown = "%s[%s]" % (name, worst_lang)
            else:
                name_shown = name
            # A near-budget number can still be a lie in the WIDE direction:
            # an elastic column reports its grown request, not its need.
            # Only near-budget apps pay for the extra probes, and height
            # keeps the full-width measurement (narrow probes wrap taller).
            if W - mw <= RETEST_PX:
                mw, elastic = elastic_floor(name, W, H, worst_lang, mw)
                if elastic:
                    name_shown += "[elastic]"
            over = mw > W or mh > H
            near = (not over) and (W - mw <= TIGHT_PX or H - mh <= TIGHT_PX)
            if over:
                failures.append((W, H, name_shown, mw, mh))
            elif near:
                tight.append((W, H, name_shown, W - mw, H - mh))
            note = ("   <<< OVERFLOWS" if over else
                    ("   <-- tight: %dpx wide, %dpx tall to spare"
                     % (W - mw, H - mh) if near else ""))
            print("  %-16s needs at least %4d x %-4d%s"
                  % (name_shown, mw, mh, note))

    if tight:
        print("\nTIGHT (fits, but with less than %dpx to spare — one longer "
              "translation from overflowing):" % TIGHT_PX)
        for W, H, name, dw, dh in tight:
            print("  %-16s %dx%d  %dpx wide / %dpx tall left"
                  % (name, W, H, dw, dh))
        print("  (a [xx] tag names the language that was widest — the plain "
              "run measures only the shipped default, which is near the "
              "shortest of the seventeen)")

    print("\nRESULT: " + ("ALL FIT" if not failures else
                          "OVERFLOWS: %s" % (failures,)))
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        try:
            print(json.dumps(measure_one(sys.argv[2], int(sys.argv[3]),
                                         int(sys.argv[4]))))
        except Exception as exc:                                # noqa: BLE001
            print(json.dumps({"error": "%s: %s"
                              % (type(exc).__name__, str(exc)[:80])}))
        raise SystemExit(0)
    raise SystemExit(main())
