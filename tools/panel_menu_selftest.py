#!/usr/bin/env python3
"""panel_menu_selftest — the menu bar's static-and-shaped contract.

Three 2026-08-10 design-owner directives are pinned here:

 1. The panel dropdown is clipped to its ROUNDED silhouette (the corner-
    transparency fix): shell.menu_shape_rects turns the menu rectangle into
    X shape rows whose corners follow the card's 12px arc, so the desktop
    shows through behind the rounded corners. The math is proven pixel-wise
    (no X server involved).
 2. The menu bar is MOTION-EXEMPT: the drop-from-the-title arrival and its
    retract stay DELETED from shell.py. Re-adding them is the regression
    this suite exists to catch.
 3. Apps fade OUT on close (system.app-close): nbapp._close_fade carries the
    inventory marker, is wired connect_after on delete-event, and completes
    synchronously on a widget with no frame clock (the no-frame-clock
    route), which is what makes it provable headless.

What this suite CANNOT see (display-owed, guest train): the actual scanned-
out corner pixels and the felt staticness of the bar. The math and the
wiring are the parts a sandbox can hold.

Red-proof: PANEL_SHELL_FILE / PANEL_NBAPP_FILE point the source checks and
module loads at a scratch copy; the built-in PASS-MUTANT checks sabotage
temp copies and assert the relevant check goes red.
"""
import os
import re
import sys
import tempfile
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
SHELL_FILE = os.environ.get("PANEL_SHELL_FILE", os.path.join(DE, "shell.py"))
NBAPP_FILE = os.environ.get("PANEL_NBAPP_FILE", os.path.join(DE, "nbapp.py"))
sys.path.insert(0, DE)

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    if not ok:
        FAILS.append(name)
        print("FAIL %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  ok %s" % name)
    return ok


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def covered(rects):
    """Pixel membership set for a rect list (small test geometries only)."""
    px = set()
    for (x, y, w, h) in rects:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                px.add((xx, yy))
    return px


def shape_checks(shell_mod, prefix=""):
    """The rounded-silhouette contract, pixel-wise. Returns True if the
    corner check held (the PASS-MUTANT drives this against a sabotage)."""
    rect = (100, 46, 200, 120)
    x, y, w, h = rect
    r = 12
    rows = shell_mod.menu_shape_rects(rect, r)
    px = covered(rows)
    area = sum(rw * rh for (_, _, rw, rh) in rows)

    corner_ok = check(prefix + "shape-corners-excluded",
                      (x, y) not in px and (x + w - 1, y) not in px
                      and (x, y + h - 1) not in px
                      and (x + w - 1, y + h - 1) not in px,
                      "a rectangle corner pixel is still scanned out")
    check(prefix + "shape-edges-included",
          (x + w // 2, y) in px and (x, y + h // 2) in px
          and (x + w // 2, y + h - 1) in px and (x + w - 1, y + h // 2) in px,
          "an edge midpoint fell outside the silhouette")
    check(prefix + "shape-no-overlap", len(px) == area,
          "rows overlap: %d px vs %d area" % (len(px), area))
    check(prefix + "shape-in-bounds",
          all(rx >= x and ry >= y and rx + rw <= x + w and ry + rh <= y + h
              for (rx, ry, rw, rh) in rows),
          "a shape row escapes the menu rectangle")
    # exact rounded-rect deficit is (4-pi)*r^2 ~= 123.7 for r=12; allow the
    # one-pixel stair either way but never "nothing cut" / "too much cut"
    deficit = w * h - len(px)
    check(prefix + "shape-area-sane", 80 <= deficit <= 170,
          "corner deficit %d px is not a 12px-arc cut" % deficit)
    tiny = shell_mod.menu_shape_rects((0, 0, 10, 8), r)
    tpx = covered(tiny)
    check(prefix + "shape-tiny-safe",
          tiny and all(rw > 0 and rh > 0 for (_, _, rw, rh) in tiny)
          and (0, 0) not in tpx,
          "a menu smaller than the radius must clamp, not crash or square off")
    check(prefix + "shape-radius-zero",
          shell_mod.menu_shape_rects(rect, 0) == [rect],
          "radius 0 must return the plain rectangle")
    return corner_ok


def main():
    shell_src = open(SHELL_FILE, encoding="utf-8").read()
    nbapp_src = open(NBAPP_FILE, encoding="utf-8").read()

    # ---- 1. rounded silhouette ----
    shell = load_module("panel_shell_probe", SHELL_FILE)
    shape_checks(shell)
    check("shape-css-radius-sync",
          shell.MENU_SHAPE_RADIUS == 12
          and re.search(r"border-radius:\s*12px", shell_src) is not None,
          "MENU_SHAPE_RADIUS and the .sysmenu border-radius drifted apart")
    check("shape-wired",
          "menu_shape_rects(self._menu_rect, MENU_SHAPE_RADIUS)" in shell_src,
          "_apply_shape no longer clips the menu to its silhouette")

    # ---- 2. the bar is motion-exempt ----
    check("menu-motion-retired",
          "nbmotion-inventory: system.panel-menu-open" not in shell_src
          and "nbmotion-inventory: system.panel-menu-close" not in shell_src
          and "_menu_arrival_draw" not in shell_src
          and "nbmotion.Damaged" not in shell_src,
          "panel menu animation machinery is back in shell.py")
    check("menu-motion-no-import",
          re.search(r"^\s*import nbmotion", shell_src, re.M) is None,
          "shell.py imports the motion engine again")

    # ---- 3. the close fade ----
    check("close-fade-marker",
          "nbmotion-inventory: system.app-close" in nbapp_src,
          "the inventory marker left nbapp.py")
    check("close-fade-wired-after",
          'connect_after("delete-event", self._close_fade)' in nbapp_src,
          "the close fade is not wired connect_after on delete-event")

    nbapp = load_module("panel_nbapp_probe", NBAPP_FILE)
    import nbmotion

    class FakeOverlay(object):
        def __init__(self):
            self.opacity = 1.0
            self.sets = []

        def get_opacity(self):
            return self.opacity

        def set_opacity(self, v):
            self.opacity = v
            self.sets.append(v)

        def get_frame_clock(self):
            return None            # forces the synchronous no-clock route

    class FakeWin(object):
        def __init__(self):
            self._overlay = FakeOverlay()
            self.destroyed = [0]

        def destroy(self):
            self.destroyed[0] += 1

    real_policy = nbmotion.policy
    try:
        nbmotion.policy = lambda tok, fade=False: 200
        t = FakeWin()
        held = nbapp.AppWindow._close_fade(t)
        if t.destroyed[0] == 0:
            # tolerate an idle-scheduled completion: pump briefly
            try:
                from gi.repository import GLib
                ctx = GLib.MainContext.default()
                for _ in range(200):
                    if t.destroyed[0]:
                        break
                    ctx.iteration(False)
            except Exception:
                pass
        check("close-fade-holds", held is True,
              "the fade path must hold the close (return True)")
        check("close-fade-destroys", t.destroyed[0] == 1,
              "destroy never landed after the fade")
        check("close-fade-opacity-zero", t._overlay.opacity == 0.0,
              "the overlay did not fade to 0")
        check("close-fade-once", nbapp.AppWindow._close_fade(t) is False,
              "a second close must fall through immediately")

        nbmotion.policy = lambda tok, fade=False: 0
        t2 = FakeWin()
        still = nbapp.AppWindow._close_fade(t2)
        check("close-fade-still-instant",
              still is False and t2.destroyed[0] == 0
              and t2._overlay.opacity == 1.0,
              "under a still policy the close must proceed untouched")
    finally:
        nbmotion.policy = real_policy

    # ---- PASS-MUTANTS: prove the checks can go red ----
    with tempfile.TemporaryDirectory() as td:
        sab = os.path.join(td, "shell_sab.py")
        with open(sab, "w", encoding="utf-8") as fh:
            fh.write(shell_src.replace("inset = int(round(r - s))",
                                       "inset = 0"))
        mod = load_module("panel_shell_sab", sab)
        rect = (100, 46, 200, 120)
        px = covered(mod.menu_shape_rects(rect, 12))
        check("PASS-MUTANT-shape", (100, 46) in px,
              "the square-corner sabotage was not visible to the corner check")

        nb_sab = nbapp_src.replace(
            'self.connect_after("delete-event", self._close_fade)', "")
        check("PASS-MUTANT-wiring",
              'connect_after("delete-event", self._close_fade)' not in nb_sab,
              "the wiring sabotage failed to remove the hook")

    print("%s: %d checks, %d failed" %
          ("FAIL" if FAILS else "PASS", CHECKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
