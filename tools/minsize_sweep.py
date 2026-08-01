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


def measure_one(name, W, H):
    """Measure one app in THIS process. Only ever called in a --one child, so
    the CSS it leaves on the screen dies with the process."""
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


def main():
    sizes = [tuple(int(v) for v in s.split("x")) for s in sys.argv[1:]] or \
        [(1024, 740), (1366, 740)]

    import finder as _finder
    apps = sorted(set(_finder.APP_MODULES.values()) | {"finder"})

    failures, tight = [], []
    for (W, H) in sizes:
        print("\n=== %dx%d ===" % (W, H))
        for name in apps:
            try:
                r = subprocess.run(
                    [sys.executable, os.path.abspath(__file__), "--one",
                     name, str(W), str(H)],
                    capture_output=True, text=True, timeout=180,
                    env=dict(os.environ))
            except subprocess.TimeoutExpired:
                print("  %-12s TIMED OUT" % name)
                continue
            line = (r.stdout or "").strip().splitlines()
            data = None
            for ln in reversed(line):
                if ln.startswith("{"):
                    try:
                        data = json.loads(ln)
                    except ValueError:
                        data = None
                    break
            if data is None or "error" in (data or {}):
                detail = (data or {}).get("error") if data else \
                    ((r.stderr or "").strip().splitlines() or ["no output"])[-1]
                print("  %-12s ERROR %s" % (name, str(detail)[:70]))
                continue
            mw, mh = data["w"], data["h"]
            over = mw > W or mh > H
            near = (not over) and (W - mw <= TIGHT_PX or H - mh <= TIGHT_PX)
            if over:
                failures.append((W, H, name, mw, mh))
            elif near:
                tight.append((W, H, name, W - mw, H - mh))
            note = ("   <<< OVERFLOWS" if over else
                    ("   <-- tight: %dpx wide, %dpx tall to spare"
                     % (W - mw, H - mh) if near else ""))
            print("  %-12s needs at least %4d x %-4d%s" % (name, mw, mh, note))

    if tight:
        print("\nTIGHT (fits, but with less than %dpx to spare — one longer "
              "translation from overflowing):" % TIGHT_PX)
        for W, H, name, dw, dh in tight:
            print("  %-12s %dx%d  %dpx wide / %dpx tall left"
                  % (name, W, H, dw, dh))

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
