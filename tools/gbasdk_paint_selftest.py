#!/usr/bin/env python3
"""Headless regression checks for the GBA SDK pixel-paint pipeline.

GBASDK_MODULE_DIR may point at a scratch copy for mutation testing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = Path(os.environ.get("GBASDK_MODULE_DIR", DEFAULT_DE))
sys.path.insert(0, str(MODULE_DIR))

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk
import gbasdk

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name +
          ("" if ok else "   <- " + str(detail)))


class Canvas:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.draws = 0

    def get_allocated_width(self): return self.width
    def get_allocated_height(self): return self.height
    def grab_focus(self): pass
    def queue_draw(self): self.draws += 1


class Undo:
    def __init__(self): self.touches = 0; self.flushes = 0
    def touch(self): self.touches += 1
    def flush(self): self.flushes += 1


def event(kind, x, y, button=1, state=0, keyval=None):
    return SimpleNamespace(type=kind, x=x, y=y, button=button,
                           state=state, keyval=keyval)


def harness(size=8, sprite=False, allocation=(211, 173)):
    app = gbasdk.GbaSdk.__new__(gbasdk.GbaSdk)
    frame = [gbasdk.TRANSPARENT] * (size * size)
    canvas = Canvas(*allocation)
    app._paint_color = 0x001f
    app._spr_tool = "pen"
    app._spr_play = None
    app._spr_cur = [0, 0]
    app._tile_cur = [0, 0]
    app._paint_stroke = None
    app.undo = Undo()
    app._save_autosave = lambda: None
    app._render_tree = lambda: None
    app._render_frame_list = lambda: None
    app._render_tile_list = lambda: None
    app._update_colour_count = lambda: None
    app._set_paint = lambda color: setattr(app, "_paint_color", color)
    if sprite:
        app._spr_canvas = canvas
        app._tile_canvas = Canvas(1, 1)
        app._cur_sprite = lambda: {"w": size, "h": size}
        app._cur_frame = lambda: frame
        handler = app._on_sprite_paint
    else:
        app._tile_canvas = canvas
        app._spr_canvas = Canvas(1, 1)
        app._cur_tileset = lambda: {"size": size}
        app._cur_tile = lambda: frame
        handler = app._on_tile_paint
    return app, canvas, frame, handler


def point(canvas, size, i, j):
    cell, ox, oy = gbasdk.GbaSdk._canvas_geom(canvas, size, size)
    return ox + (i + .5) * cell, oy + (j + .5) * cell


for size, sprite, allocation in ((8, True, (73, 121)),
                                 (32, True, (401, 119)),
                                 (8, False, (97, 63)),
                                 (16, False, (211, 173)),
                                 (32, False, (89, 307))):
    app, canvas, frame, handler = harness(size, sprite, allocation)
    x, y = point(canvas, size, size - 1, size - 2)
    handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                          state=Gdk.ModifierType.BUTTON1_MASK))
    check("%s %dx%d pen maps the addressed cell" %
          ("sprite" if sprite else "tile", size, size),
          frame[(size - 2) * size + size - 1] == app._paint_color)

app, canvas, frame, handler = harness(16, False)
x0, y0 = point(canvas, 16, 1, 4)
x1, y1 = point(canvas, 16, 12, 4)
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x0, y0,
                      state=Gdk.ModifierType.BUTTON1_MASK))
handler(canvas, event(Gdk.EventType.MOTION_NOTIFY, x1, y1,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("tile drag paints a continuous stroke",
      all(frame[4 * 16 + i] == app._paint_color for i in range(1, 13)))
before = list(frame)
x2, y2 = point(canvas, 16, 3, 9)
handler(canvas, event(Gdk.EventType.MOTION_NOTIFY, x2, y2, state=0))
check("motion without a held button paints nothing", frame == before)
app._on_paint_release(canvas, event(Gdk.EventType.BUTTON_RELEASE, x1, y1))
check("pointer release lands one stroke undo snapshot",
      app.undo.flushes == 1 and app._paint_stroke is None)

app, canvas, frame, handler = harness(32, True, (127, 289))
x0, y0 = point(canvas, 32, 2, 2); x1, y1 = point(canvas, 32, 2, 20)
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x0, y0,
                      state=Gdk.ModifierType.BUTTON1_MASK))
handler(canvas, event(Gdk.EventType.MOTION_NOTIFY, x1, y1,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("sprite drag paints a continuous stroke",
      all(frame[j * 32 + 2] == app._paint_color for j in range(2, 21)))

app, canvas, frame, handler = harness(8, True)
old, border = 3, 7
frame[:] = [old] * 64
for i in range(8): frame[3 * 8 + i] = border
app._paint_color = 9
app._spr_tool = "fill"
x, y = point(canvas, 8, 2, 1)
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("fill changes only the connected clicked region",
      frame[:24] == [9] * 24 and frame[24:32] == [border] * 8 and
      frame[32:] == [old] * 32)
touches = app.undo.touches
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("fill already at target is a no-op", app.undo.touches == touches)
single = [border] * 64; single[10] = old
app._flood_fill(single, 8, 8, 2, 1, 9)
check("fill terminates on a single cell", single.count(9) == 1)
whole = [old] * (32 * 32)
app._flood_fill(whole, 32, 32, 0, 0, 9)
check("fill terminates on a whole 32x32 frame", whole == [9] * (32 * 32))

app, canvas, frame, handler = harness(8, True)
frame[11] = 0x1234
app._spr_tool = "pick"; app._paint_color = 1
x, y = point(canvas, 8, 3, 1); before = list(frame)
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("pick selects without modifying pixels",
      app._paint_color == 0x1234 and frame == before)
app._spr_tool = "erase"
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                      state=Gdk.ModifierType.BUTTON1_MASK))
app._spr_tool = "pen"; app._paint_color = 0x001f
handler(canvas, event(Gdk.EventType.BUTTON_PRESS, x, y,
                      state=Gdk.ModifierType.BUTTON1_MASK))
check("eraser state does not leak into pen", frame[11] == 0x001f)

app, canvas, frame, _handler = harness(16, False)
app._tile_cur[:] = [15, 15]
app._on_canvas_key(canvas, event(Gdk.EventType.KEY_PRESS, 0, 0,
                                 keyval=Gdk.KEY_space))
check("tile keyboard painting uses the current tile size",
      frame[15 * 16 + 15] == app._paint_color)
app._on_canvas_key(canvas, event(Gdk.EventType.KEY_PRESS, 0, 0,
                                 keyval=Gdk.KEY_Delete))
check("keyboard erase matches mouse erase",
      frame[15 * 16 + 15] == gbasdk.TRANSPARENT)

# Closing while sprite playback is active must release its repeating source.
app, _canvas, _frame, _handler = harness(8, True)
app._spr_play = 713
app._layout_save_timer = None
app.jobs = SimpleNamespace(close=lambda: None)
removed = []
real_remove = gbasdk.GLib.source_remove
gbasdk.GLib.source_remove = lambda source_id: removed.append(source_id) or True
try:
    app._on_destroy()
finally:
    gbasdk.GLib.source_remove = real_remove
check("destroy stops and clears the sprite preview timer",
      removed == [713] and app._spr_play is None, (removed, app._spr_play))


def mutant_check():
    if os.environ.get("GBASDK_PAINT_MUTANT"):
        return
    tmp = Path(tempfile.mkdtemp(prefix="gbasdk-paint-mutant-", dir=ROOT / ".codex-scratch"))
    try:
        for source in MODULE_DIR.glob("*.py"):
            shutil.copy2(source, tmp / source.name)
        target = tmp / "gbasdk.py"
        text = target.read_text()
        needle = "self._paint_pointer(w, ev, tile, n, n)"
        if needle not in text:
            check("PASS-MUTANT suite can locate tile stride", False, needle)
            return
        target.write_text(text.replace(needle,
                                       "self._paint_pointer(w, ev, tile, 8, 8)", 1))
        env = dict(os.environ, GBASDK_MODULE_DIR=str(tmp), GBASDK_PAINT_MUTANT="1")
        run = subprocess.run([sys.executable, __file__], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check("PASS-MUTANT sabotaged tile stride makes named checks red",
              run.returncode != 0 and "FAIL tile 16x16 pen maps the addressed cell" in run.stdout,
              run.stdout[-1000:])
    finally:
        shutil.rmtree(tmp)


mutant_check()
passed = sum(results)
print("\n%d/%d checks passed" % (passed, len(results)))
raise SystemExit(0 if passed == len(results) else 1)
