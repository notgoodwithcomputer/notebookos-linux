#!/usr/bin/env python3
"""theme_transition_check — the theme ANIMATES its state changes (lively, no glass).

The design rule is simple: animate EVERY state change; the only things out are 3D
and liquid glass, because they do not fit the paper style. So this gate is a
POSITIVE one — it checks that Papertone actually eases its state changes (they are
not stripped to instant snaps) and carries the motion-inventory markers.

It used to do the opposite — forbid animating any layout property and demand
"colour and border only". That was the campaign's over-reach, not the design
intent, and it was removed 2026-08-08: a transition may animate anything GTK can,
layout included. (GTK3 CSS cannot express 3D transforms or backdrop-blur, so
"no glass" is a whole-design rule, not something this file can usefully police.)

  python3 tools/theme_transition_check.py

Red-proof: delete the button/entry state-feedback `transition-property` (state
changes would snap) and the feedback check goes red; remove a
`nbmotion-inventory:` marker and its check goes red; force the switch state into
a 0ms block and the toggle-animates check goes red.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
THEME = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                     "usr", "share", "themes", "Papertone", "gtk-3.0", "gtk.css")

_FAILS = []
_CHECKS = [0]


def _check(ok, msg):
    _CHECKS[0] += 1
    if not ok:
        _FAILS.append(msg)
        print("FAIL  %s" % msg)


def main():
    if not os.path.isfile(THEME):
        print("FAIL  theme not found: %s" % THEME)
        return 1
    src = open(THEME, encoding="utf-8").read()

    # state changes ANIMATE: the theme declares at least one transition, and a
    # short colour/border state-feedback transition (buttons, entries, toggles)
    # exists rather than snapping.
    _check(len(re.findall(r"transition-property\s*:", src)) >= 1,
           "the theme declares no transition at all — state changes would SNAP")
    feedback = re.search(
        r"transition-property\s*:[^;{}]*\bborder-color\b[^;{}]*\bcolor\b[^;{}]*;"
        r"\s*transition-duration\s*:\s*(\d+)ms", src)
    _check(feedback is not None,
           "no colour+border state-feedback transition — buttons/toggles/entries "
           "would change state with no animation")

    # a toggle's state TRAVELS (it is in the eased block and not forced to 0ms).
    eased = re.search(
        r"([^{}]*)\{\s*transition-property\s*:[^{}]*\bborder-color\b[^{}]*;"
        r"\s*transition-duration\s*:\s*\d+ms", src)
    _check(eased is not None and "switch" in eased.group(1),
           "the switch is not in the eased state-feedback block — it would snap "
           "rather than travel (app.any-toggle)")
    forced_instant = any(
        "switch:checked" in sel or "switch slider" in sel
        for sel in re.findall(r"([^{}]*)\{\s*transition-duration\s*:\s*0ms", src))
    _check(not forced_instant,
           "switch state is forced to 0ms — the toggle jumps (app.any-toggle)")

    # the inventory markers this theme realises are present.
    for mid in ("app.toolbar-state", "app.any-toggle"):
        _check("nbmotion-inventory: %s" % mid in src,
               "gtk.css does not carry the %s marker" % mid)

    n = _CHECKS[0]
    if _FAILS:
        print("\nRESULT: FAILED — %d of %d (theme animates its state changes)"
              % (len(_FAILS), n))
        return 1
    print("\nPASS  theme transitions: %d checks (state changes animate; markers "
          "present)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
