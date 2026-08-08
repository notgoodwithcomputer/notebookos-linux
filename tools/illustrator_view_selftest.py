#!/usr/bin/env python3
"""Headless checks for Illustrator zoom, field paint, and margin entry."""
import os
import sys

import cairo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import illustrator  # noqa: E402

fails = []
checks = [0]


def check(name, condition, detail=""):
    checks[0] += 1
    print(("ok   " if condition else "FAIL ") + name,
          "" if condition else str(detail))
    if not condition:
        fails.append(name)


steps = illustrator.ZOOM_STEPS
check("zoom minimum is the first fractional ladder step",
      illustrator.ZOOM_MIN == steps[0] == 1 / 8)
check("zoom ladder is strictly increasing", all(a < b for a, b in zip(steps, steps[1:])))
for start in range(1, len(steps) - 1):
    probe = illustrator.Illustrator.__new__(illustrator.Illustrator)
    probe.zoom = steps[start]
    probe._set_zoom = lambda z, p=probe: setattr(p, "zoom", z)
    probe._step_zoom(1)
    probe._step_zoom(-1)
    check("zoom step %d is reversible" % start,
          probe.zoom == steps[start], probe.zoom)
check("fit selects a sub-1x step for an oversized document",
      illustrator.fit_zoom(2000, 1000, 500, 500) == 1 / 4)

for z in (1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2):
    w, h = 37, 23
    corners = ((0, 0, (0, 0)),
               (w * z - 1e-9, 0, (w - 1, 0)),
               (0, h * z - 1e-9, (0, h - 1)),
               (w * z - 1e-9, h * z - 1e-9, (w - 1, h - 1)))
    check("%.4gx maps all four corners exactly" % z,
          all(illustrator.view_pixel(x, y, z, w, h) == want
              for x, y, want in corners))

surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 31, 19)
cr = cairo.Context(surface)
cr.set_source_rgb(0, 0, 0)
cr.paint()
illustrator.paint_field(cr, 31, 19)
surface.flush()
expected = illustrator.px4("#DED4C2")
data, stride = surface.get_data(), surface.get_stride()
samples = [(0, 0), (30, 0), (0, 18), (30, 18), (15, 9)]
check("field paint replaces black through every sampled outside pixel",
      all(bytes(data[y * stride + x * 4:y * stride + x * 4 + 4]) == expected
          for x, y in samples))


class Event:
    button, state = 1, 0
    def __init__(self, x, y): self.x, self.y = x, y


class Canvas:
    def queue_draw(self): pass


class Margin:
    def translate_coordinates(self, _canvas, x, y):
        return x - 10, y - 10


app = illustrator.Illustrator.__new__(illustrator.Illustrator)
app._closed = False
app.cw = app.ch = 8
app.zoom = 1 / 2
app.tool, app.size, app.color = "pencil", 1, "#1A1916"
app.sym_x = app.sym_y = app.fill_shapes = False
app.layers = [illustrator.Layer("Background", 8, 8, fill_white=True)]
app.active = 0
app.canvas, margin = Canvas(), Margin()
app._drawing = False
app._pending = app._stroke_track = None
app._undo_stack = app._redo_stack = []
app._undo_names = app._redo_names = []
app._recent = []
app._shift = False
app._start = app._last = None
app._preview = app._preview_rect = app._scratch = app._cursor = None
app._flash_save = lambda _text: None
app._dmg = lambda _rect: None
app._dmg_cursor = lambda _rect=None: None
app._refresh_status = lambda: None
app._sync_controls = lambda: None
app._mark_unsaved = lambda: None
app._on_press(margin, Event(2, 11))
check("margin press stores a clamped edge anchor", app._start == (0, 2), app._start)
app._on_motion(app.canvas, Event(1.1, 1.1))
app._on_release(app.canvas, Event(1.1, 1.1))
surf = app.layers[0].surface
surf.flush()
ink = illustrator.px4("#1A1916")
painted = set()
for y in range(8):
    for x in range(8):
        i = y * surf.get_stride() + x * 4
        if bytes(surf.get_data()[i:i + 4]) == ink:
            painted.add((x, y))
check("margin press anchors at the edge and drag-in paints",
      (0, 2) in painted and (2, 2) in painted, sorted(painted))

print("%d checks, %d failures" % (checks[0], len(fails)))
raise SystemExit(len(fails))
