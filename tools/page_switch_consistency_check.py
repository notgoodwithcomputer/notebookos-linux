#!/usr/bin/env python3
"""page_switch_consistency_check — app.page-pane-switch is consistent OS-wide.

The motion inventory's app.page-pane-switch is "directional, consistent OS-wide":
a Gtk.Stack page change slides forward to a later page and back to an earlier
one, on ONE shared primitive (nbtransitions.PageSwitcher / switch_page) so the
direction cannot drift between apps. That consistency is only real if every app
that switches a Stack's pages goes through the primitive rather than calling
`set_visible_child_name` by hand (which sets no direction and picks one
transition type for life — the very drift the entry forbids).

So this gate is the enforcement behind the entry: an app that constructs a
Gtk.Stack AND switches its pages must route the switch through nbtransitions.
Seven apps already do (academics, cookbook, language, packages, sequencer,
settings, video); the four that still hand-roll are a both-direction RATCHET
(grid_check's house style) — a NEW hand-rolled switch fails, and a debt app that
adopts the primitive (or drops its Stack) fails as STALE so the list cannot rot.

  python3 tools/page_switch_consistency_check.py

Red-proof: remove a name from DEBT and its app becomes an unaccounted hand-roller
(fail); add an adopter (e.g. video.py) to DEBT and it fails as stale.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")

# Apps that switch a Gtk.Stack's pages by hand instead of through the shared
# PageSwitcher — the consistency debt. module -> why it is owed / who owns it.
# Burn-down: adopt nbtransitions.PageSwitcher (direction inferred from the page
# order) and REMOVE the entry in the same change, or, if the switch is genuinely
# non-directional (a bare two-state toggle with no "later/earlier"), say so here.
DEBT = {
    "calculator.py":   "basic<->scientific Stack switched by hand — app-lane",
    "installer.py":    "install-STEP Stack switched by hand; the step order is a "
                       "direction, so PageSwitcher would slide it — B lane",
    "gbaworkspace.py": "workspace panes switched by hand — gba-loop lane",
    "gbasdk.py":       "SDK workspace panes switched by hand — gba-loop lane",
}

_PRIMITIVE = ("PageSwitcher", "switch_page")
_SWITCH_CALLS = {"set_visible_child_name", "set_visible_child"}

_FAILS = []
_CHECKS = [0]


def _check(ok, msg):
    _CHECKS[0] += 1
    if not ok:
        _FAILS.append(msg)
        print("FAIL  %s" % msg)


def _switches_a_stack(tree):
    """True when the module BOTH constructs a Gtk.Stack and calls a
    set_visible_child[_name] on something — i.e. it changes a Stack's page."""
    has_stack = False
    switches = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "Stack":
                has_stack = True
            elif node.func.attr in _SWITCH_CALLS:
                switches = True
    return has_stack and switches


def _uses_primitive(tree):
    """Return true only for an executable shared-transition call.

    Source-text membership let comments, strings and unused imports claim an
    adoption that never happened.  Calls through either an imported name or a
    module/object attribute are the useful, conservative boundary here.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in _PRIMITIVE:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PRIMITIVE:
            return True
    return False


def main():
    seen_debt = {}
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py") or fn == "nbtransitions.py":
            continue                     # nbtransitions DEFINES the primitive
        path = os.path.join(DE, fn)
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        if not _switches_a_stack(tree):
            continue
        hand_rolled = not _uses_primitive(tree)
        if fn in DEBT:
            seen_debt[fn] = hand_rolled
            if hand_rolled:
                _CHECKS[0] += 1
                print("debt  %-16s %s" % (fn, DEBT[fn]))
            else:
                _check(False, "%s now routes its Stack switch through the "
                       "primitive — STALE debt, remove its DEBT entry" % fn)
        else:
            _check(not hand_rolled,
                   "%s hand-rolls a Gtk.Stack page switch (set_visible_child*) "
                   "instead of nbtransitions.PageSwitcher — direction will drift "
                   "app-to-app; adopt the primitive or record the debt" % fn)
    for fn in sorted(set(DEBT) - set(seen_debt)):
        _check(False, "STALE DEBT: %s no longer switches a Gtk.Stack — remove "
               "its DEBT entry" % fn)

    n = _CHECKS[0]
    if _FAILS:
        print("\nRESULT: FAILED — %d of %d (page-switch consistency)"
              % (len(_FAILS), n))
        return 1
    print("\nPASS  page-switch consistency: %d checks (%d adopters clean, "
          "%d ratcheted debt)"
          % (n, n - len(seen_debt), sum(1 for v in seen_debt.values() if v)))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
