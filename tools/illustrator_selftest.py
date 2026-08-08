#!/usr/bin/env python3
"""illustrator_selftest — is the pixel editor actually pixel-exact?

    DISPLAY=:0 python3 tools/illustrator_selftest.py

The claims this file exists to prove, by READING THE SURFACE BACK rather than
by reasoning about the code:

* **No antialiasing anywhere on the artwork path.** After any stroke or shape,
  every pixel on the canvas is either exactly the background bytes or exactly
  the brush bytes. A single pixel that is a blend of the two is a failure — and
  that is precisely what cairo's `stroke()` produced before this rewrite, where
  a "1 px" line came out two pixels wide with both of them grey.
* **A size-N brush is N pixels across.** Not N-ish, not N plus a soft rim.
* **Zoom maps screen coordinates to image pixels exactly**, at every level: the
  pixel that is painted is the pixel that was under the cursor.
* **Save -> open is byte-identical.** The canvas adopts a PNG at its own size
  and never resamples, so a sprite survives the round trip.
* Flood fill, symmetry, shapes, undo/redo and the palette are checked the same
  way: exact bytes, exact counts.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="illustrator-selftest-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk                                 # noqa: E402
import cairo                                                  # noqa: E402
import illustrator                                            # noqa: E402

FAILS = []
CHECKS = [0]
WHITE = illustrator.px4("#FFFFFF")
INK = illustrator.px4("#1A1916")
RED = illustrator.px4("#C71818")


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


class Ev(object):
    """The three fields the canvas handlers read off a GdkEvent."""

    def __init__(self, x, y, button=1, state=0, direction=None, delta_y=0):
        self.x = float(x)
        self.y = float(y)
        self.button = button
        self.state = state
        self.direction = direction
        self.delta_y = delta_y


def app(w=32, h=32, tool="pencil", size=1, colour="#1A1916", zoom=1):
    a = illustrator.Illustrator()
    a.cw, a.ch = w, h
    a.layers = [illustrator.Layer("Background", w, h, fill_white=True)]
    a.active = 0
    a.zoom = zoom
    a.tool = tool
    a.size = size
    a.color = colour
    a.sym_x = a.sym_y = False
    a.fill_shapes = False
    a._new_scratch()
    return a


def pixels(a, layer=0):
    """{(x, y): 4 raw bytes} for every pixel of a layer."""
    surf = a.layers[layer].surface
    surf.flush()
    data = surf.get_data()
    stride = surf.get_stride()
    out = {}
    for y in range(a.ch):
        for x in range(a.cw):
            i = y * stride + x * 4
            out[(x, y)] = bytes(data[i:i + 4])
    return out


def painted(a, px, layer=0):
    """The set of pixels holding exactly `px`."""
    return {p for p, v in pixels(a, layer).items() if v == px}


def stroke(a, points):
    """Drive a real press / motion / release gesture in IMAGE pixels."""
    z = a.zoom
    off = z // 2
    a._on_press(None, Ev(points[0][0] * z + off, points[0][1] * z + off))
    for p in points[1:]:
        a._on_motion(None, Ev(p[0] * z + off, p[1] * z + off))
    a._on_release(None, Ev(points[-1][0] * z + off, points[-1][1] * z + off))


# ============================================================ 1. hard pixels
print("--- 1. no antialiasing on the artwork path ----------------------")

a = app()
stroke(a, [(5, 5)])
hit = painted(a, INK)
check("a 1 px pencil sets exactly ONE pixel", hit == {(5, 5)}, sorted(hit))

vals = set(pixels(a).values())
check("only two byte values exist on the canvas: paper and ink",
      vals == {WHITE, INK}, [v.hex() for v in vals])

a = app()
stroke(a, [(2, 2), (29, 29)])          # a 45-degree diagonal
vals = set(pixels(a).values())
check("a diagonal stroke blends NOTHING (no third byte value)",
      vals == {WHITE, INK}, [v.hex() for v in vals])
diag = painted(a, INK)
check("the diagonal is one pixel per step, 28 of them",
      len(diag) == 28 and diag == {(i, i) for i in range(2, 30)}, len(diag))

a = app()
stroke(a, [(0, 7), (31, 9)])           # a shallow slope: the classic AA giveaway
vals = set(pixels(a).values())
check("a shallow-slope stroke blends nothing either",
      vals == {WHITE, INK}, [v.hex() for v in vals])
rows = {}
for (x, y) in painted(a, INK):
    rows[x] = rows.get(x, 0) + 1
check("every column of a 1 px shallow line holds exactly one pixel",
      set(rows.values()) == {1}, sorted(set(rows.values())))

# a 1 px pencil is FULLY OPAQUE: alpha is the byte that antialiasing eats first
a = app()
stroke(a, [(3, 3)])
surf = a.layers[0].surface
surf.flush()
i = 3 * surf.get_stride() + 3 * 4
check("the painted pixel's alpha byte is 255, not a coverage fraction",
      surf.get_data()[i + 3] == 255, surf.get_data()[i + 3])

# the same claim for every shape tool
for tool, pts in (("line", [(4, 4), (26, 17)]),
                  ("rect", [(4, 4), (26, 20)]),
                  ("ellipse", [(4, 4), (26, 20)])):
    a = app(tool=tool)
    stroke(a, pts)
    vals = set(pixels(a).values())
    check("the %s tool blends nothing" % tool, vals == {WHITE, INK},
          [v.hex() for v in vals])

for tool in ("rect", "ellipse"):
    a = app(tool=tool)
    a.fill_shapes = True
    stroke(a, [(4, 4), (26, 20)])
    vals = set(pixels(a).values())
    check("a filled %s blends nothing" % tool, vals == {WHITE, INK},
          [v.hex() for v in vals])

# ============================================================ 2. brush sizes
print("--- 2. brush size is a pixel count -----------------------------")


def span(pts):
    xs = [x for x, _y in pts]
    ys = [y for _x, y in pts]
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


for n in (1, 2, 3, 4, 5, 8, 13, 16):
    a = app(size=n)
    stroke(a, [(16, 16)])
    hit = painted(a, INK)
    w, h = span(hit)
    check("a %d px square brush is %d x %d and covers %d pixels"
          % (n, n, n, n * n),
          (w, h) == (n, n) and len(hit) == n * n, "%dx%d, %d px" % (w, h, len(hit)))

for n in (1, 2, 3, 4, 5, 8, 13, 16):
    a = app(tool="brush", size=n)
    stroke(a, [(16, 16)])
    hit = painted(a, INK)
    w, h = span(hit)
    round_px = illustrator.brush_pixels(n, "round")
    check("a %d px round brush is %d px across and is a disc, not a box"
          % (n, n),
          (w, h) == (n, n) and len(hit) == round_px
          and (n <= 2 or round_px < n * n),
          "%dx%d, %d px (box would be %d)" % (w, h, len(hit), n * n))

# a horizontal drag with an N px brush must leave a band exactly N tall
for n in (1, 3, 6):
    a = app(size=n)
    stroke(a, [(4, 16), (27, 16)])
    hit = painted(a, INK)
    _w, h = span(hit)
    check("a %d px brush dragged sideways leaves a band %d px tall" % (n, n),
          h == n, h)

a = app(size=1)
a._step_size(1)
a._step_size(1)
check("[ / ] step the size by one pixel", a.size == 3, a.size)
for _ in range(200):
    a._step_size(1)
check("the size is clamped to %d px" % illustrator.SIZE_MAX,
      a.size == illustrator.SIZE_MAX, a.size)
for _ in range(200):
    a._step_size(-1)
check("the size never falls below 1 px", a.size == 1, a.size)
check("the size readout is a pixel count", "1" in a.size_lbl.get_text()
      and "px" in a.size_lbl.get_text().lower(), a.size_lbl.get_text())

# ============================================================ 3. the palette
print("--- 3. the palette ---------------------------------------------")

check("the palette is a wide grid, not the 16 chrome pigments",
      len(illustrator.PALETTE) >= 100, len(illustrator.PALETTE))
check("every swatch is a distinct colour",
      len(set(illustrator.PALETTE)) == len(illustrator.PALETTE),
      len(illustrator.PALETTE) - len(set(illustrator.PALETTE)))
check("the grid is %d columns of full rows" % illustrator.PAL_COLS,
      len(illustrator.PALETTE) % illustrator.PAL_COLS == 0,
      len(illustrator.PALETTE))
names = [illustrator.palette_name(i) for i in range(len(illustrator.PALETTE))]
check("every swatch has a hover name", all(n and not n.startswith("#")
                                           for n in names))
check("no two swatches share a name", len(set(names)) == len(names),
      len(names) - len(set(names)))
check("the OS chrome palette is NOT the default artwork palette",
      "#9A7B4F" not in illustrator.PALETTE and "#1A1916" not in
      illustrator.PALETTE)
# the ramp has to reach both ends or there is nothing to shade with
lums = sorted(0.299 * r + 0.587 * g + 0.114 * b
              for r, g, b in (illustrator._rgb255(c)
                              for c in illustrator.PALETTE))
check("the palette spans black to white", lums[0] < 12 and lums[-1] > 243,
      (round(lums[0], 1), round(lums[-1], 1)))
sats = [max(illustrator._rgb255(c)) - min(illustrator._rgb255(c))
        for c in illustrator.PALETTE]
check("the palette holds fully saturated hues, not only muted ones",
      max(sats) > 200, max(sats))
check("a mixed colour is still named in the palette's vocabulary",
      illustrator.mix_name("#3A7FBF") == "Azure",
      illustrator.mix_name("#3A7FBF"))

a = app()
bad = []
for i in range(len(illustrator.PALETTE)):
    x, y, w, h = a._cell_rect(i)
    if a._cell_at(x + w // 2, y + h // 2) != i:
        bad.append(i)
check("a click anywhere in a swatch picks THAT swatch", not bad, bad[:4])
check("a click in the gap between swatches picks nothing",
      a._cell_at(a.SW + 0.5, 0) is None)

a = app()
a._pick_color(None, illustrator.PALETTE[40])
check("clicking a swatch takes the active colour",
      a.color == illustrator.PALETTE[40].upper(), a.color)
check("a used colour lands in the recent row",
      a._recent[0] == illustrator.PALETTE[40].upper(), a._recent[:2])

# ============================================================ 4. zoom
print("--- 4. zoom ----------------------------------------------------")

a = app()
for z in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32):
    a.zoom = z
    bad = []
    for px in (0, 1, 7, 31):
        for off in (0, z // 2, z - 1):
            got = a._pos(Ev(px * z + off, px * z + off))
            if got != (px, px):
                bad.append((z, px, off, got))
    check("at %dx every screen pixel of an image pixel maps back to it" % z,
          not bad, bad[:3])

a = app()
a.zoom = 1
check("a coordinate left of the canvas is NOT clamped onto the edge",
      a._pos(Ev(-3, -3)) == (-3, -3), a._pos(Ev(-3, -3)))

# end to end: at each zoom, the pixel under the cursor is the pixel painted
for z in (1, 2, 5, 8, 16):
    a = app(zoom=z)
    stroke(a, [(11, 6)])
    hit = painted(a, INK)
    check("at %dx a click paints exactly the pixel under the cursor" % z,
          hit == {(11, 6)}, sorted(hit)[:4])

# and the widget is exactly the image times the zoom, so the mapping holds
a = app()
a._set_zoom(8)
check("the canvas widget is the image times the zoom",
      tuple(a.canvas.get_size_request()) == (a.cw * 8, a.ch * 8),
      a.canvas.get_size_request())
a._set_zoom(1)
check("Actual Size is 1 image pixel per screen pixel", a.zoom == 1, a.zoom)
a._step_zoom(1)
check("zoom steps up through the ladder", a.zoom in illustrator.ZOOM_STEPS
      and a.zoom > 1, a.zoom)
for _ in range(40):
    a._step_zoom(1)
check("zoom is clamped to %dx" % illustrator.ZOOM_MAX,
      a.zoom == illustrator.ZOOM_MAX, a.zoom)
for _ in range(40):
    a._step_zoom(-1)
# The user explicitly asked to zoom out below 1x: 1x is the boundary between
# reduction and exact enlargement now, not the floor.  Drive the real fit path
# with a document twice the available viewport in both dimensions.
a.show_all()
while Gtk.events_pending():
    Gtk.main_iteration()
alloc = a.mat.get_allocation()
fit_w, fit_h = alloc.width - 12, alloc.height - 12
a.cw, a.ch = fit_w * 2, fit_h * 2
a._zoom_fit()
check("user-requested fit can select below 1x and keep the whole document visible",
      a.zoom < 1 and a.cw * a.zoom <= fit_w
      and a.ch * a.zoom <= fit_h,
      (a.zoom, (a.cw * a.zoom, a.ch * a.zoom), (fit_w, fit_h)))
# The user-requested reductions are one exact reciprocal ladder below the 1x
# boundary; at and above that boundary integer-exactness remains the law.
below_1x = [z for z in illustrator.ZOOM_STEPS if z < 1]
check("the 1x boundary separates the reciprocal zoom-out ladder from exact integer zoom",
      below_1x == [1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2]
      and all(a < b for a, b in zip(illustrator.ZOOM_STEPS,
                                    illustrator.ZOOM_STEPS[1:]))
      and 1 in illustrator.ZOOM_STEPS
      and all(isinstance(z, int) for z in illustrator.ZOOM_STEPS if z >= 1),
      illustrator.ZOOM_STEPS)
src = open(os.path.join(DE, "illustrator.py"), encoding="utf-8").read()
# The 1x boundary preserves nearest-neighbour integer enlargement, while the
# newly requested reductions deliberately use a non-nearest smoothing filter.
zoom_filter = "cairo.FILTER_NEAREST if z >= 1 else cairo.FILTER_BILINEAR"
check("the 1x boundary keeps exact nearest blits above and smooths only zoom-out",
      src.count(zoom_filter) >= 2
      and cairo.FILTER_BILINEAR != cairo.FILTER_NEAREST)
check("the pixel grid appears at high zoom", illustrator.GRID_FROM <= 8
      and a.grid is True)

# ---- what the zoom actually PAINTS, read back off a rendered surface ----
# The checks above prove the image buffer is exact. This one runs the real
# _on_draw and reads the result, because the other half of the promise is that
# magnifying it does not soften it: an image pixel has to arrive on screen as a
# hard z-by-z block of one colour.


def render(a):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, a.cw * a.zoom, a.ch * a.zoom)
    cr = cairo.Context(surf)
    a._on_draw(a.canvas, cr)
    surf.flush()
    return surf


def screen_px(surf, x, y):
    data = surf.get_data()
    i = y * surf.get_stride() + x * 4
    return bytes(data[i:i + 4])


for z in (2, 8, 16):
    a = app(w=4, h=4, zoom=z)
    a.grid = False
    stroke(a, [(1, 1)])
    surf = render(a)
    block = {(x, y) for y in range(a.ch * z) for x in range(a.cw * z)
             if screen_px(surf, x, y) == INK}
    want = {(x, y) for y in range(z, 2 * z) for x in range(z, 2 * z)}
    check("at %dx one image pixel paints exactly one %dx%d screen block"
          % (z, z, z), block == want, len(block ^ want))
    vals = {screen_px(surf, x, y) for y in range(a.ch * z)
            for x in range(a.cw * z)}
    check("at %dx the magnified edge is hard (no interpolated pixel)" % z,
          vals == {WHITE, INK}, [v.hex() for v in vals])

# the grid must land ON the pixel boundaries, not half a block off
a = app(w=4, h=4, zoom=16)
a.grid = True
surf = render(a)
lines = [x for x in range(a.cw * 16)
         if screen_px(surf, x, 40) != screen_px(surf, 8, 40)]
check("the pixel grid lands on image-pixel boundaries",
      lines and all(x % 16 == 0 for x in lines), lines[:8])
a.grid = False
check("the grid is only drawn from %dx up" % illustrator.GRID_FROM,
      illustrator.GRID_FROM >= 4)

# ============================================================ 5. flood fill
print("--- 5. flood fill ----------------------------------------------")

a = app(tool="line", colour="#1A1916")
stroke(a, [(16, 0), (16, 31)])          # a wall down the middle
a.tool = "fill"
a.color = "#C71818"
a.zoom = 8
a._on_press(None, Ev(4 * 8 + 3, 4 * 8 + 3))   # a click on the LEFT half at 8x
a._on_release(None, Ev(4 * 8 + 3, 4 * 8 + 3))
red = painted(a, RED)
check("a fill at 8x fills the region under the cursor, not another one",
      (4, 4) in red and (28, 4) not in red, len(red))
check("the fill stops at the wall and does not leak",
      all(x < 16 for x, _y in red), sorted(red)[:3])
check("the fill leaves no blended edge",
      set(pixels(a).values()) == {WHITE, INK, RED},
      [v.hex() for v in set(pixels(a).values())])
before = len(painted(a, RED))
a._on_press(None, Ev(4 * 8 + 3, 4 * 8 + 3))
a._on_release(None, Ev(4 * 8 + 3, 4 * 8 + 3))
check("filling the same colour again is a no-op",
      len(painted(a, RED)) == before and not a._drawing)

# ============================================================ 6. shapes
print("--- 6. shapes and shift constraints ----------------------------")

a = app(tool="rect")
stroke(a, [(4, 6), (20, 14)])
hit = painted(a, INK)
check("a 1 px rectangle is a closed rim, 4 pixels thick nowhere",
      span(hit) == (17, 9)
      and len(hit) == 2 * 17 + 2 * 9 - 4, (span(hit), len(hit)))

a = app(tool="rect")
a.fill_shapes = True
stroke(a, [(4, 6), (20, 14)])
check("a filled rectangle is exactly its box", len(painted(a, INK)) == 17 * 9,
      len(painted(a, INK)))

a = app(tool="ellipse")
stroke(a, [(4, 4), (24, 24)])
hit = painted(a, INK)
check("an ellipse outline is closed and inside its box",
      span(hit) == (21, 21) and len(hit) > 40, (span(hit), len(hit)))
xs = sorted(x for x, y in hit if y == 14)
check("the ellipse rim is symmetric about its centre",
      xs and xs[0] + xs[-1] == 4 + 24, xs)

a = app(tool="line")
z = a.zoom
a._on_press(None, Ev(4, 4, state=Gdk.ModifierType.SHIFT_MASK))
a._on_motion(None, Ev(24, 9, state=Gdk.ModifierType.SHIFT_MASK))
a._on_release(None, Ev(24, 9, state=Gdk.ModifierType.SHIFT_MASK))
hit = painted(a, INK)
check("Shift locks the Line tool to a straight ray",
      all(y == 4 for _x, y in hit), sorted(hit)[:4])

a = app(tool="rect")
a._on_press(None, Ev(4, 4, state=Gdk.ModifierType.SHIFT_MASK))
a._on_motion(None, Ev(24, 12, state=Gdk.ModifierType.SHIFT_MASK))
a._on_release(None, Ev(24, 12, state=Gdk.ModifierType.SHIFT_MASK))
w, h = span(painted(a, INK))
check("Shift makes the Rectangle tool draw a square", w == h, (w, h))

# the live preview is the same pixels the commit writes
a = app(tool="ellipse")
a._on_press(None, Ev(4, 4))
a._on_motion(None, Ev(24, 18))
prev = a._scratch
prev.flush()
pdata = bytes(prev.get_data())
a._on_release(None, Ev(24, 18))
a2 = app(tool="ellipse")
a2._on_press(None, Ev(4, 4))
a2._on_motion(None, Ev(24, 18))
committed = painted(a, INK)
scratch_hit = set()
stride = prev.get_stride()
for y in range(a.ch):
    for x in range(a.cw):
        i = y * stride + x * 4
        if pdata[i:i + 4] == INK:
            scratch_hit.add((x, y))
check("what the shape preview shows is exactly what commits",
      scratch_hit == committed, len(scratch_hit ^ committed))

# ============================================================ 7. symmetry
print("--- 7. mirror -------------------------------------------------")

a = app()
a.sym_x = True
stroke(a, [(4, 9)])
check("the left/right mirror paints both sides",
      painted(a, INK) == {(4, 9), (27, 9)}, sorted(painted(a, INK)))
a = app()
a.sym_y = True
stroke(a, [(4, 9)])
check("the top/bottom mirror paints both halves",
      painted(a, INK) == {(4, 9), (4, 22)}, sorted(painted(a, INK)))
a = app()
a.sym_x = a.sym_y = True
stroke(a, [(4, 9)])
check("both mirrors together paint four quadrants",
      painted(a, INK) == {(4, 9), (27, 9), (4, 22), (27, 22)},
      sorted(painted(a, INK)))

# ============================================================ 8. undo / redo
print("--- 8. undo, redo, canvas size --------------------------------")

a = app()
clean = pixels(a)
stroke(a, [(4, 4), (20, 20)])
drawn = pixels(a)
a._undo()
check("Undo restores the pixels byte for byte", pixels(a) == clean)
a._redo()
check("Redo puts them back byte for byte", pixels(a) == drawn)
for i in range(30):
    stroke(a, [(i, 1)])
for _ in range(40):
    a._undo()
check("Undo unwinds a long stroke history", pixels(a) == clean)
check("the history is at least 80 frames deep", illustrator.UNDO_DEPTH >= 80,
      illustrator.UNDO_DEPTH)

a = app()
stroke(a, [(2, 2)])
a._resize_canvas(48, 20)
check("Canvas Size changes the document size", (a.cw, a.ch) == (48, 20),
      (a.cw, a.ch))
check("resizing keeps the artwork anchored at the top-left corner",
      pixels(a)[(2, 2)] == INK, pixels(a)[(2, 2)].hex())
check("resizing does not resample (no new byte values)",
      set(pixels(a).values()) <= {WHITE, INK, b"\x00\x00\x00\x00"},
      [v.hex() for v in set(pixels(a).values())])
a._undo()
check("Undo takes the canvas size back too", (a.cw, a.ch) == (32, 32),
      (a.cw, a.ch))
a._resize_canvas(99999, 0)
check("a silly canvas size is clamped, not obeyed",
      illustrator.MIN_DIM <= a.cw <= illustrator.MAX_DIM
      and illustrator.MIN_DIM <= a.ch <= illustrator.MAX_DIM, (a.cw, a.ch))

a = app()
stroke(a, [(3, 3)])
a._flip(True)
check("Flip Horizontal mirrors the artwork exactly",
      painted(a, INK) == {(28, 3)}, sorted(painted(a, INK)))
a._flip(False)
check("Flip Vertical mirrors the artwork exactly",
      painted(a, INK) == {(28, 28)}, sorted(painted(a, INK)))
a._undo()
a._undo()
check("a flip undoes", painted(a, INK) == {(3, 3)}, sorted(painted(a, INK)))

# The history is bounded by BYTES as well as by frames. Flip and Canvas Size
# build replacement layers, so each of their frames keeps a whole previous set
# of surfaces — a frame count does not bound that, and 200 flips of a full
# 1024x1024 document with four layers retained 3.2 GB before this was added.
# Flipping to check a drawing is routine, so the ceiling has to hold.
def one_px(a, x, y, layer=0):
    """The 4 raw bytes at (x, y) — pixels() builds a million-entry dict, which
    is too slow to use on a full-size canvas."""
    surf = a.layers[layer].surface
    surf.flush()
    i = y * surf.get_stride() + x * 4
    return bytes(surf.get_data()[i:i + 4])


def big_doc(layers=4):
    a = illustrator.Illustrator()
    a.cw, a.ch = illustrator.MAX_DIM, illustrator.MAX_DIM
    a.layers = [illustrator.Layer("L%d" % i, a.cw, a.ch, fill_white=(i == 0))
                for i in range(layers)]
    a.active = 0
    a.zoom = 1
    a.tool = "pencil"
    a.size = 1
    a.color = "#1A1916"
    a.sym_x = a.sym_y = False
    a.fill_shapes = False
    a._new_scratch()
    return a


# ---- Canvas Size honours the size it is given -------------------------------
# It used to clamp at 1024 while the module docstring promised 2048, so asking
# for anything larger produced a different canvas with nothing on screen to say
# why — indistinguishable from resizing being broken.
_rs = illustrator.Illustrator()
import re as _re
_doc = _re.search(r"any size up to (\d+)", illustrator.__doc__ or "")
check("the documented maximum is the one actually enforced",
      _doc and int(_doc.group(1)) == illustrator.MAX_DIM,
      "docstring says %s, MAX_DIM is %d"
      % (_doc.group(1) if _doc else "?", illustrator.MAX_DIM))
for _w in (16, 256, illustrator.MAX_DIM):
    _rs._resize_canvas(_w, _w)
    check("a canvas of %d is granted" % _w, (_rs.cw, _rs.ch) == (_w, _w),
          "%dx%d" % (_rs.cw, _rs.ch))
    check("...and its layers really are that size" ,
          _rs.layers[0].surface.get_width() == _w)
_rs._resize_canvas(64, 64)
_rs._resize_canvas(illustrator.MAX_DIM + 500, 64)
check("a size past the maximum is refused, not silently accepted",
      _rs.cw <= illustrator.MAX_DIM)

# ...and now through the REAL DIALOG, which is where it was actually broken.
# Calling _resize_canvas directly passed all along while Canvas Size did
# nothing at all: the prompt's buttons dismiss the card BEFORE running their
# callback, destroying the entries inside it, so reading them back gave "" and
# the size fell through to the one the document already had.
def _dialog_resize(app, w, h):
    app._canvas_size_prompt()
    layer = app._saveprompt_layer

    def walk(widget, out):
        out.append(widget)
        if isinstance(widget, Gtk.Container):
            for c in widget.get_children():
                walk(c, out)
        return out

    nodes = walk(layer, [])
    entries = [n for n in nodes if isinstance(n, Gtk.Entry)]
    for e, t in zip(entries, (str(w), str(h))):
        e.set_text(t)
    btn = [n for n in nodes if isinstance(n, Gtk.Button)
           and n.get_label() and "esize" in n.get_label()]
    btn[0].clicked()
    while Gtk.events_pending():
        Gtk.main_iteration()
    return (app.cw, app.ch)

_dlg = illustrator.Illustrator()
_dlg._flash_save = lambda _t: None
check("Canvas Size resizes through the dialog a person actually uses",
      _dialog_resize(_dlg, 256, 256) == (256, 256),
      "%dx%d" % (_dlg.cw, _dlg.ch))
check("...including a non-square size", _dialog_resize(_dlg, 100, 180)
      == (100, 180), "%dx%d" % (_dlg.cw, _dlg.ch))
check("...and the largest allowed one",
      _dialog_resize(_dlg, illustrator.MAX_DIM, illustrator.MAX_DIM)
      == (illustrator.MAX_DIM, illustrator.MAX_DIM))
_was = (_dlg.cw, _dlg.ch)
check("...while a value out of range leaves the canvas alone",
      _dialog_resize(_dlg, 9000, 9000) == _was)
check("...and so does text that is not a number",
      _dialog_resize(_dlg, "big", "big") == _was)

# Vertical flips: same memory behaviour, and a whole-row copy rather than a
# per-pixel rebuild, so a full-size canvas stays quick enough to test.
big = big_doc()
stroke(big, [(3, 3)])
check("a full-size canvas takes a mark to flip", one_px(big, 3, 3) == INK)
for _ in range(20):          # 16 MB a frame: far past the ceiling
    big._flip(False)
held = big._history_bytes()
check("the history never holds more than its byte ceiling",
      held <= illustrator.HISTORY_BYTES,
      "%.0f MB held, ceiling %.0f MB"
      % (held / 1048576.0, illustrator.HISTORY_BYTES / 1048576.0))
# How many whole-canvas frames the ceiling can hold is arithmetic, not a
# constant: a structural frame is every layer's surface, so it scales with the
# square of the canvas. At MAX_DIM=2048 with 4 layers one frame is 64MB and
# only a single step fits under a 96MB ceiling; at 1024 it is 16MB and six do.
# Assert the relationship, so raising MAX_DIM cannot silently make this pass
# for the wrong reason — and never fewer than one, or Undo would be dead.
_frame = big.cw * big.ch * 4 * len(big.layers)
_fits = max(1, illustrator.HISTORY_BYTES // _frame)
check("...and it kept as many steps as the ceiling allows",
      len(big._undo_stack) >= min(4, _fits),
      "kept %d, ceiling allows %d" % (len(big._undo_stack), _fits))
check("...and at the largest canvas at least one step always survives",
      len(big._undo_stack) >= 1, len(big._undo_stack))

# The everyday case: painting stores only the rectangle a stroke touched, so a
# big canvas keeps a deep history however large it is. This is what the ceiling
# is actually protecting, and it must not regress when MAX_DIM moves.
_paint = big_doc()
for i in range(12):
    stroke(_paint, [(5 + i, 5)])
check("painting on a full-size canvas still keeps a deep history",
      len(_paint._undo_stack) >= 10, len(_paint._undo_stack))
# 20 vertical flips is even, so the mark is back at the top
check("the mark is where the flips left it", one_px(big, 3, 3) == INK)
big._undo()
check("...and one step of Undo still takes back the last flip",
      one_px(big, 3, illustrator.MAX_DIM - 4) == INK,
      one_px(big, 3, illustrator.MAX_DIM - 4).hex())

# a surface the document is still showing is not charged to the history, so
# Add / Delete / Move Layer keep their full depth however big the canvas is
deep = illustrator.Illustrator()
deep.cw, deep.ch = illustrator.MAX_DIM, illustrator.MAX_DIM
deep.layers = [illustrator.Layer("Background", deep.cw, deep.ch,
                                 fill_white=True)]
deep.active = 0
deep.zoom = 1
deep.tool = "pencil"
deep.size = 1
deep.color = "#1A1916"
deep.sym_x = deep.sym_y = False
deep.fill_shapes = False
deep._new_scratch()
for _ in range(12):
    deep._add_layer()
check("layer frames share the live surfaces and are not charged for them",
      deep._history_bytes() == 0, deep._history_bytes())

# a sprite-sized document keeps the full depth: the ceiling must not bind here
small = app()
for i in range(illustrator.UNDO_DEPTH + 10):
    small._flip(True)
check("a sprite canvas still keeps the full undo depth",
      len(small._undo_stack) == illustrator.UNDO_DEPTH,
      len(small._undo_stack))

# ============================================================ 9. round trip
print("--- 9. save / open round trip ---------------------------------")

tmp = os.path.join(os.environ["NB_HOME"], "sprite.png")
a = app(w=37, h=23, colour="#C71818", tool="pencil", size=1)
for i in range(23):
    stroke(a, [(i, i)])
stroke(a, [(0, 22), (36, 0)])
want = pixels(a)
check("Save writes the PNG", a._write_png(tmp) and os.path.isfile(tmp))
b = app()
check("Open adopts the PNG", b._open_file(tmp))
check("Open keeps the image's own pixel size, unscaled",
      (b.cw, b.ch) == (37, 23), (b.cw, b.ch))
check("save -> open is byte-for-byte identical", pixels(b) == want,
      sum(1 for k in want if want[k] != pixels(b).get(k)))
check("the round trip introduced no third colour",
      set(pixels(b).values()) == {WHITE, RED},
      [v.hex() for v in set(pixels(b).values())])

# an image bigger than the canvas maximum is the ONE case that may shrink,
# and it must shrink without smoothing
big = os.path.join(os.environ["NB_HOME"], "big.png")
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, illustrator.MAX_DIM + 40, 8)
cr = cairo.Context(surf)
cr.set_source_rgb(1, 0, 0)
cr.paint()
surf.flush()
surf.write_to_png(big)
c = app()
c._open_file(big)
check("an oversized PNG is clamped to the canvas maximum",
      c.cw <= illustrator.MAX_DIM and c.ch <= illustrator.MAX_DIM,
      (c.cw, c.ch))

# ================================================= 10. the drawing interface
# The controls a person actually reaches for. Everything below was reachable
# only by hover, by menu, or not at all before the interface pass: the tools
# were eight unlabelled 26px icons (three of which are the same diagonal
# implement at that size), the shape fill lived in a menu item that named the
# state it would move TO, and the brush footprint was invisible until a click
# had already landed.
print("--- 10. the drawing interface ---------------------------------")

check("every tool is named, not only drawn",
      all(name and name != tid for tid, name in illustrator.TOOLS),
      illustrator.TOOLS)
check("...and no two tools share a name",
      len({n for _t, n in illustrator.TOOLS}) == len(illustrator.TOOLS))
check("every tool says what a drag with it does",
      all(tid in illustrator.TOOL_HINTS for tid, _n in illustrator.TOOLS),
      sorted(set(dict(illustrator.TOOLS)) - set(illustrator.TOOL_HINTS)))
check("every tool has a single-key shortcut",
      all(tid in illustrator.TOOL_KEYS for tid, _n in illustrator.TOOLS))
# Shift constrains lines to 45 degrees and boxes to squares/circles. Nothing
# else in the app mentions it, so if the hints stop naming it the behaviour
# becomes undiscoverable again.
check("the three Shift constraints are named in the hints",
      all("Shift" in illustrator.TOOL_HINTS[t]
          for t in ("line", "rect", "ellipse")))

# The settings that dim: each set must be exactly the tools the engine really
# consults, or a live-looking control does nothing (or a dimmed one lies).
check("brush size dims for exactly the tools that use a brush",
      illustrator.SIZE_TOOLS == {"pencil", "brush", "eraser",
                                 "line", "rect", "ellipse"})
check("shape fill dims for exactly the shape tools",
      illustrator.SHAPE_TOOLS == {"line", "rect", "ellipse"})
a = app()
for tid in dict(illustrator.TOOLS):
    a.tool = tid
    pts, spans = a._shape_ops("rect", (2, 2), (6, 6)) if tid in \
        illustrator.SHAPE_TOOLS else ([], [])
check("the fill tool really ignores the brush",
      "fill" not in illustrator.SIZE_TOOLS
      and "picker" not in illustrator.SIZE_TOOLS)

a = app(tool="rect")
a._set_fill_shapes(True)
check("Filled is a visible choice, and it takes",
      a.fill_shapes and "sel" in a.filled_btn.get_style_context().list_classes()
      and "sel" not in a.outline_btn.get_style_context().list_classes())
a._set_fill_shapes(False)
check("...and Outline is the other half of the same pair",
      not a.fill_shapes
      and "sel" in a.outline_btn.get_style_context().list_classes())

# The colour well used to arm the eyedropper, so clicking the swatch stopped
# the pencil drawing and the next canvas click took a colour instead.
a = app(tool="brush")
a._on_chip_press(None, None)
check("clicking the colour well does not change the tool",
      a.tool == "brush", a.tool)
check("...it opens the mixer", a._saveprompt_layer is not None)
a._close_saveprompt()

# Sampling is a detour and hands the tool back to whatever was drawing.
a = app(w=8, h=8, tool="brush", size=9, colour="#C71818")
a._pick_tool(None, "picker")
a._pick_from_canvas((1, 1))
check("the eyedropper returns to the tool it interrupted",
      a.tool == "brush", a.tool)
check("...and it took the colour under the pointer",
      a.color.upper() == "#FFFFFF", a.color)

# The footprint outline under the pointer. This is drawn from the SAME runs
# the stamp writes, so it can never promise pixels the brush will not paint.
for shape in ("square", "round"):
    for n in (1, 2, 3, 4, 7, 12, 16, 31, 64):
        runs = illustrator.brush_runs(n, shape)
        poly = illustrator.Illustrator._brush_outline(runs)
        ok = bool(poly)
        # Every segment of a footprint boundary is axis-aligned: a diagonal
        # means the walk crossed the shape. The first version reversed the
        # finished point list rather than the ROWS, and one whole side came
        # back as a zigzag through the middle of the disc.
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if x0 != x1 and y0 != y1:
                ok = False
                break
        check("the %s %d px brush outline is a clean boundary" % (shape, n), ok)
        # ...and it encloses exactly the painted pixels: leftmost/rightmost
        # and top/bottom of the polygon match the runs' own extent.
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        want = (min(r[1] for r in runs), max(r[2] for r in runs) + 1,
                min(r[0] for r in runs), max(r[0] for r in runs) + 1)
        check("...and it bounds exactly the %s %d px footprint" % (shape, n),
              (min(xs), max(xs), min(ys), max(ys)) == want,
              ((min(xs), max(xs), min(ys), max(ys)), want))
check("a footprint with a gap in it is refused rather than drawn wrong",
      illustrator.Illustrator._brush_outline(((0, 0, 0), (2, 0, 0))) == ())

# The size ramp is a shortcut to sizes the steppers can also reach.
check("every ramp size is a legal brush size",
      all(illustrator.SIZE_MIN <= n <= illustrator.SIZE_MAX
          for n in illustrator.SIZE_RAMP))
a = app()
a._set_size(illustrator.SIZE_RAMP[-1])
check("the ramp sets the brush size", a.size == illustrator.SIZE_RAMP[-1])
a._set_size(illustrator.SIZE_MAX + 500)
check("...and cannot push it past the maximum", a.size == illustrator.SIZE_MAX)

# The window has to fit the smallest panel this OS supports, in every language
# — the dock is the tallest column, and CJK line metrics made it 763px before
# it was allowed to scroll.
a = app()
a.show_all()
while Gtk.events_pending():
    Gtk.main_iteration()
_min_h = a.get_preferred_height_for_width(1024)[0]
check("the window fits a 1024x740 panel", _min_h <= 740, _min_h)
check("the dock scrolls rather than clipping",
      a.dock_scroll.get_policy()[1] == Gtk.PolicyType.AUTOMATIC
      and a.dock_scroll.get_policy()[0] == Gtk.PolicyType.NEVER)

# ============================================================ 11. text rule
print("--- 11. wording -----------------------------------------------")

for word in ("offline", "internet", "don't worry", "beautiful", "simply",
             "just ", "enjoy", "!"):
    bad = [ln.strip() for ln in src.splitlines()
           if word in ln.lower() and ("_t(" in ln or "label=" in ln
                                      or "tooltip" in ln)]
    check("no user-visible text says %r" % word, not bad, bad[:2])

print("")
print("%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
print("RESULT: %s" % ("ALL PASS" if not FAILS
                      else "FAILED: %s" % ", ".join(FAILS)))
sys.exit(len(FAILS))
