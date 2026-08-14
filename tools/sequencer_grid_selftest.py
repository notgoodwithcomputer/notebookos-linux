#!/usr/bin/env python3
"""Headless pixel contract for Sequencer's letterpress tape grid.

The suite never creates a GTK window.  It calls the real lane and ruler paint
methods on cairo ImageSurfaces, and SEQUENCER_MODULE_DIR can point the complete
test at a scratch copy of sequencer.py for red proofs.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import cairo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
MODULE_DIR = os.environ.get("SEQUENCER_MODULE_DIR", DE)
sys.path.insert(0, MODULE_DIR)
if MODULE_DIR != DE:
    sys.path.insert(1, DE)

os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="seq-grid-home-"))
import sequencer as seq                                      # noqa: E402

failed = []
checks = 0


def check(name, condition, detail=""):
    global checks
    checks += 1
    print(("PASS " if condition else "FAIL ") + name +
          ((" — " + detail) if detail else ""))
    if not condition:
        failed.append(name)


def lum(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def pixel(surface, x, y):
    surface.flush()
    stride = surface.get_stride()
    data = surface.get_data()
    off = int(y) * stride + int(x) * 4
    # Cairo ARGB32 is BGRA in memory on the little-endian release target.
    return tuple(data[off + i] for i in (2, 1, 0))


class App:
    bpm = 120
    length = 32.0
    view_start = 0.0
    snap = 0.5
    loop_s = 0.0
    loop_e = 0.0
    loop_on = False

    def sec_per_beat(self):
        return 60.0 / self.bpm

    def sec_per_bar(self):
        return self.sec_per_beat() * seq.BEATS_PER_BAR

    def snap_seconds(self):
        return self.snap * self.sec_per_beat() if self.snap else 0.0

    def view_span(self):
        return self.span

    def snap_span(self, a, b):
        return a, b


class LaneHarness:
    def __init__(self, app, width):
        self.app, self.width = app, width

    def _x_of(self, t):
        return (t - self.app.view_start) / self.app.view_span() * self.width


def lane_surface(span, snap=0.5, width=320, height=64):
    app = App()
    app.span, app.snap = span, snap
    lane = LaneHarness(app, width)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*seq.SURF)
    cr.paint()
    seq.Lane._paint_grid(lane, cr, width, height)
    return surf, lane


def darkness(surface, x, y=24):
    return lum(tuple(v * 255 for v in seq.SURF)) - lum(pixel(surface, x, y))


fine, lane = lane_surface(4.0)
bar_x = round(lane._x_of(2.0))
beat_x = round(lane._x_of(0.5))
sub_x = round(lane._x_of(0.25))
bar_d, beat_d, sub_d = (darkness(fine, x) for x in
                         (bar_x, beat_x, sub_x))
check("GRID-BAR-DARKER bar lines darker than beat lines",
      bar_d >= beat_d + 12,
      "darkness bar %.1f beat %.1f" % (bar_d, beat_d))
check("GRID-BEAT-DARKER beat lines darker than subdivision lines",
      beat_d >= sub_d + 12,
      "darkness beat %.1f subdivision %.1f" % (beat_d, sub_d))

sep = pixel(fine, 13, 63)
sep_d = darkness(fine, 13, 63)
check("GRID-LANE-SEPARATOR lane separator is a visible paper rule",
      sep_d >= 8, "separator darkness %.1f" % sep_d)
check("GRID-LANE-TONE lane separator tone differs from time-line tone",
      sep != pixel(fine, beat_x, 24) and sep != pixel(fine, bar_x, 24),
      "separator %r bar %r beat %r" %
      (sep, pixel(fine, bar_x, 24), pixel(fine, beat_x, 24)))

coarse, coarse_lane = lane_surface(16.0)
coarse_sub = round(coarse_lane._x_of(0.25))
check("GRID-COARSE-SUBDIVISIONS subdivisions absent at coarse zoom",
      pixel(coarse, coarse_sub, 24) == tuple(round(v * 255) for v in seq.SURF),
      "pixel %r" % (pixel(coarse, coarse_sub, 24),))

free, free_lane = lane_surface(4.0, seq.SNAP_FREE)
free_sub = round(free_lane._x_of(0.25))
check("GRID-FREE-NO-FIELD FREE snap renders no subdivision field",
      pixel(free, free_sub, 24) == tuple(round(v * 255) for v in seq.SURF),
      "pixel %r" % (pixel(free, free_sub, 24),))


class Allocation:
    width = 320
    height = 30


class Widget:
    def get_allocation(self):
        return Allocation()


class RulerHarness:
    def __init__(self, span):
        self.app = App()
        self.app.span = span
        self._drag = None

    def _axis(self):
        return 0, 320

    def _x_of(self, t):
        return t / self.app.span * 320


rail = tuple(round(v * 255) for v in seq.RAIL)
folio_counts = []
for ruler_span in (16.0, 4.0):
    ruler = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 30)
    seq.Ruler._draw(RulerHarness(ruler_span), Widget(), cairo.Context(ruler))
    # The first folio is drawn just right of x=0. Ticks occupy x=0/... only;
    # ink in the intervening label box is therefore numeral ink.
    folio_pixels = [pixel(ruler, x, y) for y in range(7, 21)
                    for x in range(4, 18)]
    folio_counts.append(sum(p != rail for p in folio_pixels))
check("RULER-FOLIO-NUMERALS ruler still draws its numerals",
      min(folio_counts) >= 5,
      "coarse/fine changed pixels %s" % folio_counts)


if not os.environ.get("SEQUENCER_GRID_MUTANT_CHILD") and not failed:
    scratch_root = os.path.join(REPO, ".codex-scratch")
    os.makedirs(scratch_root, exist_ok=True)
    mutant_dir = tempfile.mkdtemp(prefix="054-grid-mutant-", dir=scratch_root)
    source = os.path.join(DE, "sequencer.py")
    target = os.path.join(mutant_dir, "sequencer.py")
    shutil.copy2(source, target)
    with open(target, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("GRID_BAR_ALPHA = 0.24", "GRID_BAR_ALPHA = 0.09", 1)
    text = text.replace("GRID_BEAT_ALPHA = 0.12", "GRID_BEAT_ALPHA = 0.0", 1)
    text = text.replace("GRID_SUB_ALPHA = 0.04", "GRID_SUB_ALPHA = 0.09", 1)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    env = dict(os.environ, SEQUENCER_MODULE_DIR=mutant_dir,
               SEQUENCER_GRID_MUTANT_CHILD="1")
    run = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                         text=True, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    killed = (run.returncode != 0 and "FAIL GRID-BAR-DARKER" in run.stdout
              and "FAIL GRID-BEAT-DARKER" in run.stdout)
    check("PASS-MUTANT flattened hierarchy makes named checks red", killed,
          "child exit %d" % run.returncode)

print("RESULT %d checks, %d failures" % (checks, len(failed)))
raise SystemExit(1 if failed else 0)
