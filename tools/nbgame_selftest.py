#!/usr/bin/env python3
"""nbgame_selftest — the emulator picture fits the panel it is actually on.

Run as:  python3 tools/nbgame_selftest.py     (display-free)

WHY THIS EXISTS
---------------
GameSession launched vbam with a hardcoded `-f 17` — 4x, a fixed 960x640
window — whatever the machine. On the 1366x768 laptops this OS targets that
was reasonable; on 1920x1080 it is half the screen; on the HiDPI panels now
supported it is a postage stamp in a field of black; and on anything
narrower than 960 the game OVERFLOWED the panel. ROADMAP #41 sat unfixable
for weeks because no harness had ever executed a ROM ("a video-path change
that cannot be run is a guess"); the execution harness landed 2026-08-07 and
the per-filter output sizes below were measured on the real build-tree vbam
under batch gdb, not read from documentation.

The mapping under test (vendored src/sdl/filters.h; host-measured):
    -f 0 -> 240x160   -f 1 -> 480x320   -f 14 -> 720x480
    -f 17 -> 960x640  -f 20 -> 1200x800 -f 21 -> 1440x960
Plain nearest stretch STOPS at 4x; 5x/6x exist only as xbrz (smoothing).
pick_scale_filter stays nearest up to 4x and takes the xbrz tier only when
five-plus factors fit — that trade is recorded in release/1.0/HANDOFF.md.

Red-proved: hardwiring the choice back to "17" fails the small-panel pins,
the two big-panel pins and the never-overflow sweep, each naming its panel.
"""
import ast
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

# Assigned, not setdefault: isolation a caller can switch off is not
# isolation (task 010).
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbgame-selftest-")

import nbgame  # noqa: E402

fails = []
ran = []


def check(cond, message):
    print(("ok   " if cond else "FAIL ") + message)
    ran.append(message)
    if not cond:
        fails.append(message)


FACTOR = {"0": 1, "1": 2, "14": 3, "17": 4, "20": 5, "21": 6}

# -- the panels this OS actually meets ---------------------------------------
# Parity first: both small shipped panels keep exactly today's picture.
check(nbgame.pick_scale_filter(1024, 722) == "17",
      "1024x722 (smallest budget) keeps the 4x picture")
check(nbgame.pick_scale_filter(1366, 768) == "17",
      "1366x768 keeps the 4x picture")
# The defect being retired: big and HiDPI panels stop getting 960x640.
check(nbgame.pick_scale_filter(1920, 1080) == "21",
      "1920x1080 takes 6x (1440x960), not half the screen")
check(nbgame.pick_scale_filter(3840, 2160) == "21",
      "3840x2160 takes 6x, not a postage stamp")
check(nbgame.pick_scale_filter(1280, 800) == "20",
      "1280x800 takes 5x (1200x800 fits exactly; the stage owns the panel)")
# And the overflow nobody had noticed: panels the fixed 960x640 never fit.
check(nbgame.pick_scale_filter(800, 600) == "14",
      "800x600 gets 3x (720x480); the old fixed 4x overflowed it")
check(nbgame.pick_scale_filter(640, 480) == "1",
      "640x480 gets 2x")
check(nbgame.pick_scale_filter(200, 150) == "0",
      "a panel smaller than the GBA screen gets 1x, the least harm")

# -- invariants, not constants ------------------------------------------------
# Never overflow: across a lattice of panel sizes, the chosen filter's output
# fits. This is the user-facing promise; the pins above are examples of it.
bad = []
w = nbgame.GBA_W
while w <= 3900:
    h = nbgame.GBA_H
    while h <= 2200:
        f = FACTOR[nbgame.pick_scale_filter(w, h)]
        if nbgame.GBA_W * f > w or nbgame.GBA_H * f > h:
            bad.append((w, h, f))
        h += 89
    w += 97
check(not bad, "no panel in a 240..3900 x 160..2200 lattice overflows "
      + ("" if not bad else "(first: %dx%d chose %dx)" % bad[0]))

# Best fill within the tier rule: factor is k itself up to 4, else min(k, 6).
bad = []
w = nbgame.GBA_W
while w <= 3900:
    h = nbgame.GBA_H
    while h <= 2200:
        k = min(w // nbgame.GBA_W, h // nbgame.GBA_H)
        want = max(1, k if k <= 4 else min(k, 6))
        got = FACTOR[nbgame.pick_scale_filter(w, h)]
        if got != want:
            bad.append((w, h, got, want))
        h += 89
    w += 97
check(not bad, "the factor is always the largest its tier allows "
      + ("" if not bad else "(first: %dx%d chose %dx, tier allows %dx)"
         % bad[0]))

# -- the default path actually uses the panel --------------------------------
_real = nbgame._screen_size
try:
    nbgame._screen_size = lambda parent: (1920, 1080)
    s = nbgame.GameSession(None, "/bin/true", "/nonexistent.gba",
                           lambda *a: None)
    check(s.scale_filter == "21",
          "GameSession with no explicit filter picks from the real panel")
    s2 = nbgame.GameSession(None, "/bin/true", "/nonexistent.gba",
                            lambda *a: None, scale_filter="17")
    check(s2.scale_filter == "17",
          "an explicit caller choice is respected unchanged")
finally:
    nbgame._screen_size = _real

# -- and the launch line consumes the choice ----------------------------------
# Belt over the behavior checks above: _launch's command must be built from
# self.scale_filter, or the picker is decoration.
src = open(os.path.join(DE, "nbgame.py"), encoding="utf-8").read()
tree = ast.parse(src)
uses = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_launch":
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute)
                    and sub.attr == "scale_filter"
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"):
                uses = True
check(uses, "_launch builds the vbam command from self.scale_filter")

print("%s — %d/%d checks passed"
      % ("FAIL" if fails else "PASS", len(ran) - len(fails), len(ran)))
sys.exit(1 if fails else 0)
