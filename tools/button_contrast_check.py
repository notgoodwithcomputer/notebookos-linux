#!/usr/bin/env python3
"""
button_contrast_check — can you actually READ every button in the OS?

WHY. GTK's `*` selector matches a Gtk.Button's LABEL node directly, so a colour
set on the button never reaches its text. An app writing the obvious thing --

    .mybutton { background: #1A1916; color: #FCFBF8; }

-- gets ink text on an ink slab, and nothing anywhere complains: the CSS is
valid, the app constructs, the widget renders. It is only visible to a person
looking at the screen, which is exactly the class of defect the rest of this
tool-set exists to catch earlier.

This constructs every app, walks every Gtk.Button in it, and compares the
button's computed background against its LABEL's computed colour using the WCAG
relative-luminance formula. Anything under 3:1 is reported; under 1.5:1 is
effectively invisible.

    tools/guestrun.sh python3 tools/button_contrast_check.py
    tools/guestrun.sh python3 tools/button_contrast_check.py finder settings
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(_HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/tmp/nbhome-contrast")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

sys.path.insert(0, _HERE)
import uishot  # noqa: E402

AA_LARGE = 3.0        # WCAG AA for large / bold text
INVISIBLE = 1.5       # below this the label is simply not there


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgba):
    r, g, b = rgba
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _rgba(ctx, prop):
    v = ctx.get_property(prop, Gtk.StateFlags.NORMAL)
    return (v.red * 255, v.green * 255, v.blue * 255), v.alpha


def _label_of(w):
    if isinstance(w, Gtk.Label):
        return w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            r = _label_of(c)
            if r is not None:
                return r
    return None


def _buttons(w, out):
    if isinstance(w, Gtk.Button):
        out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            _buttons(c, out)
    return out


def _effective_bg(start):
    """The surface the TEXT is actually read against.

    The nearest filled node at or above `start`, which is the LABEL -- not the
    button. A flat/borderless button is the normal case in this design and its
    text is read against whatever surface it sits on, so a transparent fill has
    to keep walking outward; reporting those as 1:1 would bury the real
    failures in noise.

    Starting at the BUTTON was wrong, and wrong in the direction that makes a
    gate lie. A button here routinely contains a card, and a node inside that
    card can carry its own fill: language.py's course badge is a Gtk.Label with
    class .codebadge whose background is installed at RUNTIME by a CssProvider
    (".codebadge { background: %s }" % UNIT_COLORS[i][0]). Beginning the walk
    at the button stepped straight over that fill and paired the badge's
    paper-white text with the button's paper-white ground -- so the five course
    codes EO/ES/FR/SR/ZH were reported at 1.00:1 as INVISIBLE, this tool's
    worst verdict and its exit-1 trigger, when they are white-on-green discs
    and among the most legible things on the screen (rendered and looked at,
    tools/language_shots.py 01-home). A probe must not report a verdict it did
    not earn: the failure mode is not a missed defect but a manufactured one,
    and the cost is someone "fixing" a correct design to satisfy it."""
    w = start
    for _ in range(8):
        ctx = w.get_style_context()
        rgb, alpha = _rgba(ctx, "background-color")
        if alpha > 0.35:
            return rgb
        w = w.get_parent()
        if w is None:
            break
    return (252, 251, 248)          # paper, the OS's ground


def check_app(mod_name):
    import importlib
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    m = importlib.import_module(mod_name)
    cls = None
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and issubclass(obj, Gtk.Window) \
                and obj.__module__ == m.__name__:
            cls = obj
            break
    if cls is None:
        return []
    win = cls()
    bad = []
    for btn in _buttons(win, []):
        lab = _label_of(btn)
        if lab is None or not (lab.get_text() or "").strip():
            continue
        fg, alpha = _rgba(lab.get_style_context(), "color")
        if alpha < 0.1:
            continue
        bg = _effective_bg(lab)
        r = ratio(fg, bg)
        if r < AA_LARGE:
            bad.append((r, lab.get_text()[:28],
                        "#%02X%02X%02X" % tuple(int(round(x)) for x in fg),
                        "#%02X%02X%02X" % tuple(int(round(x)) for x in bg),
                        " ".join(btn.get_style_context().list_classes())[:40]))
    try:
        win.destroy()
    except Exception:                                             # noqa: BLE001
        pass
    return bad


APPS = ["academics", "accounting", "bills", "calculator", "calendar", "contacts",
        "cookbook", "ebook", "g2048", "gbaemu", "gbasdk", "illustrator",
        "installer", "journal", "language", "maps", "mealplanner", "media",
        "music", "novel", "packages", "screenplay", "sequencer", "settings",
        "sysmon", "tasks", "terminal", "usbwriter", "video", "workout",
        "writer", "finder"]


def main():
    apps = sys.argv[1:] or APPS
    uishot.load_theme()
    total = worst = 0
    gone = 0                  # under INVISIBLE: a defect by any standard
    worst_line = None
    for name in apps:
        try:
            bad = check_app(name)
        except Exception as exc:                                  # noqa: BLE001
            print("ERR  %-13s %s" % (name, str(exc)[:70]))
            continue
        for r, text, fg, bg, classes in sorted(bad):
            total += 1
            tag = "INVISIBLE" if r < INVISIBLE else "low"
            if r < INVISIBLE:
                gone += 1
            print("%-9s %-13s %5.2f:1  %-20s fg=%s bg=%s  [%s]"
                  % (tag, name, r, repr(text), fg, bg, classes))
            if worst_line is None or r < worst_line[0]:
                worst_line = (r, name, text)
    # The two numbers answer different questions and must not be read as one.
    # Under 1.5:1 the label is NOT ON THE SCREEN -- a defect at any threshold,
    # in any theme, for any reader. Between 1.5 and 3.0 is faint secondary text,
    # which is often a deliberate hierarchy choice (an empty slot's "Add"
    # placeholder) and is a design-triage backlog, not a ship-blocker. Printing
    # only the combined count let a run with zero real defects look identical to
    # one with five.
    print("\n%d button label(s) under %.1f:1, of which %d INVISIBLE (under %.1f:1)"
          % (total, AA_LARGE, gone, INVISIBLE))
    if worst_line:
        print("worst: %.2f:1  %s  %r" % worst_line)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
