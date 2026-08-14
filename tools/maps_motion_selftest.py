#!/usr/bin/env python3
"""Behavioral gate for Maps' cached viewport motion.

MAPS_MODULE_DIR deliberately controls both the imported module and the source
examined by this test. This makes red-proof copies honest mutants rather than a
test importing the real module while reading (or mutating) another file.
"""
import math
import os
import sys

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DE = os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
DE = os.environ.get("MAPS_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)

import maps  # noqa: E402


passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("ok  ", name)
    else:
        failed += 1
        print("FAIL", name + ((": " + detail) if detail else ""))


class Pack:
    cell_deg = 1.0
    path = "/tmp/selftest.nbm2"
    directory = {}
    dir = directory


gtk_ok, _argv = Gtk.init_check()
win = None
if gtk_ok:
    win = Gtk.Window()
    canvas = Gtk.DrawingArea()
    canvas.set_size_request(640, 420)
    win.add(canvas)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
else:
    class StrictCanvas:
        """Displayless equivalent of the exact DrawingArea API under test."""
        def __init__(self):
            self.damage = []

        def get_allocated_width(self):
            return 640

        def get_allocated_height(self):
            return 420

        def get_scale_factor(self):
            return 1

        def queue_draw_area(self, x, y, width, height):
            self.damage.append((x, y, width, height))

    canvas = StrictCanvas()

app = maps.Maps.__new__(maps.Maps)
app.canvas = canvas
app.pack = Pack()
app.cx, app.cy, app.scale = 10.0, 20.0, 1000.0
app._surface = None
app._surf_size = None
app._surf_scale = None
app._surf_dev = None
app._surf_cx = app._surf_cy = 0.0
app._view_anim = None
app._view_gen = 0
app._view_moving = False
app._hi = None
app._empty = None
app._save_cfg = lambda: None
app._min_scale = lambda: 1.0

renders = []


def render(aw, ah, sf=1):
    renders.append((app.cx, app.cy, app.scale))
    app._surface = cairo.ImageSurface(cairo.FORMAT_RGB24, aw, ah)
    app._surface.set_device_scale(sf, sf)
    app._surf_size = (aw, ah)
    app._surf_scale = app.scale
    app._surf_dev = sf
    app._surf_cx, app._surf_cy = app.cx, app.cy


app._render_surface = render
calls = []


class Pending:
    def cancel(self):
        return None


real_animate = maps.nbmotion.animate


def capture(widget, on_frame, start, end, duration=None, easing=None,
            fade=False, on_done=None):
    calls.append({"widget": widget, "frame": on_frame, "start": start,
                  "end": end, "duration": duration, "easing": easing,
                  "done": on_done})
    return Pending()


maps.nbmotion.animate = capture
try:
    fx, fy = 137.0, 91.0
    old_anchor = app._to_merc(fx, fy, canvas.get_allocated_width(),
                              canvas.get_allocated_height())
    reached = app._zoom(1.4, fx, fy)
finally:
    maps.nbmotion.animate = real_animate

check("real zoom path reaches the motion primitive", len(calls) == 1,
      "animate calls=%d" % len(calls))
call = calls[0] if calls else None
check("viewport motion receives the PAGE token",
      call is not None and call.get("duration") == maps.nbmotion.PAGE
      and call.get("duration", 0) > 0,
      "[not reached: no captured primitive]" if call is None
      else "duration=%r" % call.get("duration"))
check("viewport motion receives the lively ARRIVE easing",
      call is not None and call.get("easing") is maps.nbmotion.ARRIVE,
      "[not reached: no captured primitive]" if call is None
      else "easing=%r" % call.get("easing"))

if call is not None:
    call["frame"](0.5)
    mid_scale = app.scale
    # Rendering during travel must use the cached surface. _draw is the real
    # cairo path, on a real GTK widget; a fake widget would silently miss this.
    out = cairo.ImageSurface(cairo.FORMAT_RGB24, 640, 420)
    maps.Maps._draw(app, canvas, cairo.Context(out))
    anchor_mid = app._to_merc(fx, fy, canvas.get_allocated_width(),
                              canvas.get_allocated_height())
    check("zoom interpolates scale geometrically",
          abs(mid_scale - math.sqrt(1000.0 * 1400.0)) < 1e-6,
          "mid-scale=%r" % mid_scale)
    check("pointer anchor stays fixed during the tween",
          max(abs(anchor_mid[0] - old_anchor[0]),
              abs(anchor_mid[1] - old_anchor[1])) < 1e-10,
          "anchor moved from %r to %r" % (old_anchor, anchor_mid))
    check("intermediate draw is one cached blit, not a vector rerender",
          len(renders) == 1, "render count=%d" % len(renders))
    call["frame"](1.0)
    call["done"](True)
    landed = app._to_merc(fx, fy, canvas.get_allocated_width(),
                          canvas.get_allocated_height())
    check("landing is exact and preserves the pointer anchor",
          app.scale == 1400.0
          and max(abs(landed[0] - old_anchor[0]),
                  abs(landed[1] - old_anchor[1])) < 1e-12,
          "scale=%r anchor=%r" % (app.scale, landed))
else:
    for name in ("zoom interpolates scale geometrically",
                 "pointer anchor stays fixed during the tween",
                 "intermediate draw is one cached blit, not a vector rerender",
                 "landing is exact and preserves the pointer anchor"):
        check(name, False, "[not reached: no captured primitive]")

try:
    with open(os.path.join(DE, "maps.py"), encoding="utf-8") as fh:
        source = fh.read()
except OSError as exc:
    source = ""
    source_error = str(exc)
else:
    source_error = ""
check("named content.maps transition is present",
      "# nbmotion-inventory: content.maps" in source,
      source_error or "marker absent")

if win is not None:
    win.destroy()
print("\n%d checks, %d passed, %d FAILED" % (passed + failed, passed, failed))
raise SystemExit(1 if failed else 0)
