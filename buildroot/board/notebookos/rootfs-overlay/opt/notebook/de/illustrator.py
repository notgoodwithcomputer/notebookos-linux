#!/usr/bin/env python3
"""
Illustrator — the Notebook OS pixel-art editor (native GTK).

WHY THIS IS NOT A CAIRO PAINT APP
---------------------------------
Every stroke is written straight into the layer's ARGB32 pixel buffer, byte by
byte, by the stamp/line/span routines below. Nothing on the artwork path goes
through a cairo path, because cairo ANTIALIASES: `cr.arc` / `cr.line_to` +
`stroke()` resolve geometry onto the pixel grid and a half-pixel offset comes
out as two half-covered pixels. `set_antialias(ANTIALIAS_NONE)` shrinks that
but does not remove it — the coverage rule still decides which pixels a
sub-pixel-wide edge lands on, and a "1 px" line came out two pixels wide and
grey. A pixel editor has to own the buffer:

  * `brush_runs(size, shape)` is the brush as a set of horizontal RUNS in a
    size x size box, so a size-N brush is exactly N pixels across;
  * `_line_points` is integer Bresenham, `_ellipse_spans` / `_ellipse_outline`
    are integer scanline shapes;
  * `_stamp_on` / `_spans_on` write the 4 premultiplied bytes with a slice
    assignment, which REPLACES the pixel — full alpha, no blending, ever.

The one and only consequence: a painted pixel is either exactly the brush
colour or exactly untouched. tools/illustrator_selftest.py asserts that by
reading the surface back.

LAYOUT
------
One left dock, the canvas on a slate mat, the Layers panel on the right, a
status bar below. There is no ribbon: the dock reads top to bottom as the
sentence a drawing tool is used in — PICK A TOOL, SET IT UP, CHOOSE A COLOUR —
and everything that decides what a stroke looks like lives in it. What used to
be a fifth band across the top was brush size and mirror (both stroke settings,
so they belong beside the tool), the active colour (which sat at the far right
while the palette that feeds it sat at the far left), and zoom — a VIEW control
whose percentage the status bar was already printing, so it now lives there as
a stepper instead of being shown twice. Dropping the band gave the canvas 76 px
of height back, which is most of a laptop panel's spare room.

The tools are named, not just drawn. Eight 26 px unlabelled icons is a memory
test, and three of the eight (pencil, brush, eyedropper) are the same diagonal
implement at 16 px — so the toolbox is a 2-column grid of icon-plus-name and
the status bar spells out what a drag with the current tool does, which is also
the only place Shift's constrain-to-45/square/circle was ever going to be
discovered.

The document is a small pixel canvas (64x64 by default, any size up to 2048 via
Image > Canvas Size) shown at an INTEGER zoom with FILTER_NEAREST, so one image
pixel is a hard square block of screen pixels; a pixel grid appears from 8x up.
Painting divides the event coordinate by the zoom, so the mapping is exact at
every level. The brush footprint is outlined under the pointer at that same
mapping, so the pixels a click is about to change are visible before it lands.

File I/O is PNG under $NB_HOME/Pictures. A PNG is adopted at its own size and
never resampled, so save -> open is pixel-for-pixel identical.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango  # noqa: E402

import cairo
import io
import math
import os
import time

import nbapp
import nbpicker
import nbicons
import nbtransitions
from nbi18n import _t  # noqa: E402

# The document starts as a small sprite canvas, which is what the zoom is for.
DEFAULT_CW, DEFAULT_CH = 64, 64
# 1024 is the largest canvas allowed, and the reason is the flood fill: it is a
# Python scanline fill, so filling a whole canvas costs a couple of operations
# per pixel. At 1024x1024 (1M pixels) that is about a second; at 2048x2048 it
# was four and a half, which is a frozen window. Nothing this app is for — a
# sprite, a tile, an icon — comes close to the cap.
# The canvas size Image > Canvas Size will accept. MAX_DIM was 1024 while the
# module docstring above promised 2048 and the dialog offered no limit at all,
# so asking for anything larger silently produced a 1024 canvas with nothing on
# screen to say why — which is indistinguishable from resizing being broken.
# 2048 is the documented figure and what the entry now accepts; a layer at that
# size is 16MB of ARGB32, which is why it is a limit at all rather than free.
MIN_DIM, MAX_DIM = 1, 2048
# The two side columns. The dock grew (it absorbed the ribbon) and the Layers
# panel shrank: at 272 it was the widest column in the window while holding one
# row of text, and the canvas is what both of them are there to serve.
PANEL_W = 240     # layers panel
DOCK_W = 252      # left dock: tools, tool settings, colour
# Brush size is a pixel count, not a preset.
SIZE_MIN, SIZE_MAX = 1, 64
# Five tips to click, so going from a 1 px pencil to a 16 px block is one press
# rather than fifteen. They are DRAWN at their relative size in the current
# brush shape, which is also the only place the round/square tip is visible.
SIZE_RAMP = (1, 2, 4, 8, 16)
# A stylus at rest still reports a little pressure, and a hand at full lean
# rarely reaches 1.0. Treat the middle of the range as the useful part: below
# PEN_FLOOR is the thinnest mark the tip can make, above PEN_CEIL is the chosen
# size. Without this a normal drawing hand only ever reaches two-thirds width.
PEN_FLOOR, PEN_CEIL = 0.04, 0.85


def pen_size(size, pressure):
    """The brush width for one pressure sample, in image pixels.

    Pressure drives WIDTH, not opacity. This engine writes exact pixel values
    and never blends — that is what makes its edges hard (see `_stamp_on`) —
    so a translucent mark is not something it can express. Width is also the
    honest analogue: pressing a real pencil harder broadens the mark.

    The chosen brush size is the CEILING, reached at a firm press; the floor is
    always 1 px so a feathered stroke tapers to a hairline instead of vanishing
    and leaving a gap in the line."""
    top = max(SIZE_MIN, min(SIZE_MAX, int(size)))
    if top <= SIZE_MIN:
        return top
    try:
        p = float(pressure)
    except (TypeError, ValueError):
        return top
    if p != p:                      # NaN: a driver reporting an unset axis
        return top
    p = max(0.0, min(1.0, p))
    span = PEN_CEIL - PEN_FLOOR
    frac = 1.0 if p >= PEN_CEIL else (p - PEN_FLOOR) / span
    if frac < 0.0:
        frac = 0.0
    # round-half-up across the whole range, so the ceiling is actually reachable
    return max(SIZE_MIN, min(top, int(SIZE_MIN + frac * (top - SIZE_MIN) + 0.5)))
# Enlargement stays on integer steps, where every image pixel is an exact
# screen-pixel block. Reduction necessarily shares screen pixels; those steps
# exist so a document larger than the field can still be seen as a whole.
ZOOM_MIN, ZOOM_MAX = 1 / 8, 32
ZOOM_STEPS = (1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2,
              1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32)
# Draw the pixel grid from here up; below it the lines eat the artwork.
GRID_FROM = 8


def view_pixel(x, y, zoom, width, height, clamp=False):
    """Map display coordinates to one document pixel.

    `clamp` is for a gesture begun on the surrounding field: its anchor is the
    nearest edge pixel, while ordinary canvas motion remains unclamped so a
    brush may travel naturally out of bounds and return."""
    px = int(math.floor(float(x) / float(zoom)))
    py = int(math.floor(float(y) / float(zoom)))
    if clamp:
        px = max(0, min(int(width) - 1, px))
        py = max(0, min(int(height) - 1, py))
    return px, py


def fit_zoom(width, height, available_width, available_height):
    """Largest standard zoom step that fits the whole document."""
    limit = min(float(available_width) / width,
                float(available_height) / height)
    choices = [z for z in ZOOM_STEPS if z <= limit]
    return choices[-1] if choices else ZOOM_MIN


def paint_field(cr, width, height):
    """Paint every pixel behind the document with the canvas-field colour."""
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgb(*_rgb("#DED4C2"))
    cr.rectangle(0, 0, width, height)
    cr.fill()

# Bound the Undo/Redo history. A frame stores only the RECTANGLE an edit
# actually touched (see _begin_edit / _commit_edit), so an ordinary brush stroke
# costs a few hundred bytes on a sprite canvas.
UNDO_DEPTH = 200
# ...but a STRUCTURAL frame (Flip, Canvas Size) cannot store a rectangle: those
# ops build replacement layers, so the frame keeps the whole previous set of
# surfaces. A frame COUNT does not bound that. Flipping horizontally to check a
# drawing is routine pixel-art practice, and 200 flips of a full 1024x1024
# document with four layers retained 3.2 GB — an out-of-memory kill, taking the
# unsaved artwork with it. So the history is also bounded by the pixel bytes it
# keeps alive that the document itself does not. At sprite sizes this never
# binds (200 frames of 64x64 is 3 MB) and the full depth stays; it only trims
# the extreme canvases, which is exactly where the depth was unaffordable.
HISTORY_BYTES = 96 * 1024 * 1024

# User files (File > Open / Save / Save As) are PNGs under $NB_HOME/Pictures.
NB_HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
PICS_DIR = os.path.join(NB_HOME, "Pictures")
# Colours the user reached for, kept between sessions.
CFG_FILE = os.path.join(NB_HOME, ".config", "notebook", "illustrator.json")
RECENT_MAX = 16

# (id, name) in the order the toolbox lays them out, reading across two
# columns: the two that draw, the two that remove and flood, then the three
# shapes and the sampler.
TOOLS = [
    ("pencil", "Pencil"), ("brush", "Brush"),
    ("eraser", "Eraser"), ("fill", "Fill"),
    ("line", "Line"), ("rect", "Rectangle"),
    ("ellipse", "Ellipse"), ("picker", "Eyedropper"),
]
# "Colour Picker" was the eyedropper's name, and beside a 112-swatch panel
# called Palette it read as the thing you pick a colour FROM. Eyedropper is
# what it is: it lifts a colour off the artwork.
TOOL_NAMES = dict(TOOLS)

# What a drag with each tool does, printed in the status bar the moment the
# tool is chosen. This is the ONLY place Shift's constrain is discoverable —
# it was in no tooltip, no menu and no label, so the app had three modifier
# behaviours (45-degree lines, squares, circles) that nothing announced.
TOOL_HINTS = {
    "pencil":  "Drag to draw. Square tip, hard edges.",
    "brush":   "Drag to draw. Round tip, hard edges.",
    "eraser":  "Drag to rub back to transparent.",
    "fill":    "Click an area to flood it with the colour.",
    "line":    "Drag end to end. Hold Shift for 45° steps.",
    "rect":    "Drag corner to corner. Hold Shift for a square.",
    "ellipse": "Drag corner to corner. Hold Shift for a circle.",
    "picker":  "Click the artwork to take that colour.",
}
# Which tools each setting in the dock actually reaches, so a setting that
# cannot affect the current tool is dimmed rather than silently inert. The
# flood fill takes neither a brush nor a mirror; the eyedropper takes nothing.
SIZE_TOOLS = {"pencil", "brush", "eraser", "line", "rect", "ellipse"}
SHAPE_TOOLS = {"line", "rect", "ellipse"}
MIRROR_TOOLS = SIZE_TOOLS

# Single-key tool shortcuts. The letter is surfaced in each tool's tooltip so
# it is discoverable; [ / ] step the brush size by one pixel, + / - / 0 / 1
# drive the zoom and G the pixel grid.
TOOL_KEYS = {
    "pencil": "P", "brush": "B", "eraser": "E", "fill": "F",
    "picker": "I", "line": "L", "rect": "R", "ellipse": "O",
}
_KEY_TOOLS = {
    Gdk.KEY_p: "pencil", Gdk.KEY_b: "brush", Gdk.KEY_e: "eraser",
    Gdk.KEY_f: "fill", Gdk.KEY_i: "picker", Gdk.KEY_l: "line",
    Gdk.KEY_r: "rect", Gdk.KEY_o: "ellipse",
}

# ---------------------------------------------------------------- the palette
# The old palette was the OS's own sixteen earth pigments — the colours the
# CHROME is built from. Correct for a window, useless for artwork: no vivid
# hue, no value ramp, nothing to shade with. This is a hue x value grid
# instead, 16 columns wide:
#
#   rows 0-4   16 hues at five value steps (darkest -> pale)
#   row  5     the same 16 hues muted, which is where the earth tones live
#   row  6     an eight-step neutral ramp + eight named staples
#
# 112 swatches, every one distinct, arranged so a column is one hue's shading
# ramp and a row is one value across the spectrum — pick a colour, then move
# down for its shadow and up for its light. Shadows shift toward blue and
# lights toward yellow, the way a painter mixes them, rather than being a
# straight saturation slide.
#
# The names are composed from a small vocabulary (16 hue words x 5 modifiers +
# 16 plain words) so every swatch names itself on hover in the interface
# language without needing 112 separate translations.
# Deliberately no "Violet" column: several languages translate Purple with
# their word for violet, so the two would hover the SAME name.
_HUES = (("Red", 0), ("Coral", 14), ("Orange", 30), ("Amber", 44),
         ("Yellow", 56), ("Lime", 82), ("Green", 122), ("Emerald", 150),
         ("Teal", 172), ("Cyan", 188), ("Azure", 205), ("Blue", 222),
         ("Indigo", 244), ("Purple", 276), ("Magenta", 305), ("Pink", 332))
# (name template, saturation, value, hue-shift target, hue-shift degrees).
# A None template means the hue word IS the name, with no modifier — it is not
# the string "%s", so the translator is never handed a bare placeholder to
# translate.
_VALUES = (("Darkest %s", 0.70, 0.28, 250, 10),
           ("Dark %s", 0.85, 0.48, 250, 5),
           (None, 0.90, 0.72, None, 0),
           ("Bright %s", 0.85, 0.95, 50, 5),
           # 4 degrees, not 9: at 9 both Amber and Yellow land exactly on the
           # 50-degree target and the two swatches come out the same colour
           ("Pale %s", 0.35, 1.00, 50, 4))
_MUTED = ("Muted %s", 0.30, 0.62)
_NEUTRALS = (("Black", 0x00), ("Ink", 0x24), ("Slate", 0x48),
             ("Grey", 0x70), ("Silver", 0x99), ("Ash", 0xBB),
             ("Paper", 0xDD), ("White", 0xFF))
_STAPLES = (("Brown", "#6B4A2F"), ("Tan", "#C9A26A"), ("Cream", "#F2E4C4"),
            ("Olive", "#6E7B3F"), ("Navy", "#20325C"), ("Maroon", "#5E1F28"),
            ("Gold", "#D9A21B"), ("Peach", "#F2B79A"))
PAL_COLS = 16
# Rows 5 and 6 are different families, so they are set off by a small gap.
PAL_BANDS = (5, 6)


def _hsv_hex(h, s, v):
    """HSV -> "#RRGGBB". Written out rather than imported from colorsys so the
    palette cannot depend on an optional stdlib module being in the image."""
    h = h % 360.0
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    r, g, b = ((c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x))[int(h // 60) % 6]
    return "#%02X%02X%02X" % (int(round((r + m) * 255)),
                              int(round((g + m) * 255)),
                              int(round((b + m) * 255)))


def _toward(h, target, amt):
    """Move hue `h` `amt` degrees along the SHORTER arc toward `target`."""
    d = ((target - h + 180.0) % 360.0) - 180.0
    if abs(d) <= amt:
        return float(target)
    return (h + (amt if d > 0 else -amt)) % 360.0


def _build_palette():
    cols, parts = [], []
    for tpl, s, v, tgt, amt in _VALUES:
        for name, hue in _HUES:
            h = _toward(hue, tgt, amt) if tgt is not None else hue
            cols.append(_hsv_hex(h, s, v))
            parts.append((tpl, name))
    tpl, s, v = _MUTED
    for name, hue in _HUES:
        cols.append(_hsv_hex(hue, s, v))
        parts.append((tpl, name))
    for name, g in _NEUTRALS:
        cols.append("#%02X%02X%02X" % (g, g, g))
        parts.append((None, name))
    for name, hex_ in _STAPLES:
        cols.append(hex_)
        parts.append((None, name))
    return cols, parts


PALETTE, PALETTE_PARTS = _build_palette()
PAL_ROWS = len(PALETTE) // PAL_COLS


def _rgb(hex_):
    h = hex_.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def _rgb255(hex_):
    h = hex_.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def px4(hex_):
    """The four bytes a fully opaque `hex_` occupies in a cairo ARGB32 buffer.

    Little-endian byte order is B, G, R, A and the format is premultiplied —
    which for alpha 255 is the colour itself. This is the ONLY place the
    artwork's byte layout is written down: the brush, the shapes, the flood
    fill and the tests all go through here, so a fill over a stroke of the same
    colour is a byte-for-byte no-op."""
    r, g, b = _rgb255(hex_)
    return bytes((b, g, r, 255))


CLEAR4 = b"\x00\x00\x00\x00"


def palette_name(index):
    """The hover name of palette cell `index`, in the interface language."""
    tpl, word = PALETTE_PARTS[index]
    return (_t(tpl) % _t(word)) if tpl else _t(word)


def mix_name(hex_):
    """A readable name for a colour that is not on the palette.

    Named after the palette colour it sits nearest, qualified when it is
    clearly lighter or deeper, so two similar chips still read as two
    different colours. With 112 swatches to match against, the nearest name is
    close enough that the qualifier almost never has to fire."""
    try:
        r, g, b = _rgb255(hex_)
    except (ValueError, IndexError):
        return hex_
    best, bestd, bestlum = None, None, 0
    for i, c in enumerate(PALETTE):
        pr, pg, pb = _rgb255(c)
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if bestd is None or d < bestd:
            best, bestd = i, d
            bestlum = 0.299 * pr + 0.587 * pg + 0.114 * pb
    if best is None:
        return hex_
    name = palette_name(best)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum - bestlum > 30:
        return _t("Bright %s") % name
    if bestlum - lum > 30:
        return _t("Dark %s") % name
    return name


# ------------------------------------------------------------- pixel geometry
_RUNS = {}


def brush_runs(size, shape):
    """The brush as horizontal RUNS: a tuple of (dy, dx0, dx1), inclusive,
    offset from the pixel under the cursor.

    A size-N brush covers exactly N pixels across: the box runs from
    -(N//2) to -(N//2)+N-1, so 1 -> the pixel itself, 2 -> that pixel and the
    one before it, 3 -> one either side. Runs rather than points because a
    whole run is one slice assignment into the buffer.

    "square" is the full box. "round" is the box's inscribed disc, which is
    convex, so it is still one run per row: a 3 px round brush is the plus
    shape a pixel artist expects, not a 3x3 block."""
    key = (int(size), shape)
    runs = _RUNS.get(key)
    if runs is not None:
        return runs
    n = max(SIZE_MIN, min(SIZE_MAX, int(size)))
    o = n // 2
    out = []
    if shape == "round" and n > 2:
        c = (n - 1) / 2.0
        # A hair inside n/2 so the corners of the box fall outside the disc;
        # exactly n/2 keeps them and a "round" 3 px brush is a square again.
        rr = (n / 2.0 - 0.15) ** 2
        for j in range(n):
            row = [i for i in range(n) if (i - c) ** 2 + (j - c) ** 2 <= rr]
            if row:
                out.append((j - o, row[0] - o, row[-1] - o))
    else:
        for j in range(n):
            out.append((j - o, 0 - o, n - 1 - o))
    runs = _RUNS[key] = tuple(out)
    return runs


def brush_pixels(size, shape):
    """How many pixels one stamp of this brush covers. Used by the tests."""
    return sum(dx1 - dx0 + 1 for _dy, dx0, dx1 in brush_runs(size, shape))


def _line_points(x0, y0, x1, y1):
    """Integer Bresenham from a to b, inclusive of both ends. No fractional
    coverage anywhere, which is the whole point."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    pts = []
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return pts
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def _ellipse_spans(x0, y0, x1, y1):
    """The FILLED ellipse inscribed in the box, as (y, xa, xb) inclusive spans.

    Solved per scanline from the ellipse equation on pixel CENTRES, so the
    result is symmetric for both odd and even boxes."""
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = (x1 - x0 + 1) / 2.0, (y1 - y0 + 1) / 2.0
    spans = []
    for y in range(y0, y1 + 1):
        dy = (y - cy) / ry
        v = 1.0 - dy * dy
        if v < 0:
            continue
        dx = math.sqrt(v) * rx
        xa = int(math.ceil(cx - dx - 0.5))
        xb = int(math.floor(cx + dx + 0.5)) - 1
        if xb < xa:
            xa = xb = int(round(cx))
        spans.append((y, max(x0, xa), min(x1, xb)))
    return spans


def _ellipse_outline(spans):
    """The 1-pixel closed rim of the filled ellipse `spans` describe.

    The first and last rows are drawn solid and each row's two edge pixels are
    joined to the previous row's, so a steep flank has no gaps in it."""
    pts = []
    prev = None
    for i, (y, xa, xb) in enumerate(spans):
        if prev is None or i == len(spans) - 1:
            pts.extend((x, y) for x in range(xa, xb + 1))
        else:
            pa, pb = prev
            for x in range(min(xa, pa), max(xa, pa) + 1):
                pts.append((x, y))
            for x in range(min(xb, pb), max(xb, pb) + 1):
                pts.append((x, y))
        prev = (xa, xb)
    return pts


def _snap45(a, b):
    """b moved onto the nearest horizontal / vertical / 45-degree ray from a.
    What Shift does to the Line tool."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return b
    ax, ay = abs(dx), abs(dy)
    if ax > 2 * ay:
        return (b[0], a[1])
    if ay > 2 * ax:
        return (a[0], b[1])
    n = max(ax, ay)
    return (a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n))


def _square(a, b):
    """b moved so the a-b box is square. What Shift does to Rectangle and
    Ellipse — the way a circle is drawn."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = max(abs(dx), abs(dy))
    return (a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n))


class StackHistory:
    """Presents this app's own two-stack history to nbapp.undo_menu_items.

    Illustrator keeps pixel/structure frames rather than whole-document
    snapshots, so it cannot use nbapp.UndoHistory — but the Edit menu must
    still be worded exactly as Novel, Journal, Academics and Screenplay word
    theirs, naming what a step would take back ("Undo Delete Layer"). This
    adapter is the six methods undo_menu_items asks for, over the stacks the
    app already keeps. Frames pushed without a name simply give the bare
    "Undo", which is right for a brush stroke."""

    def __init__(self, app):
        # the app, not its lists: File > New / Open REPLACE the stacks, and a
        # held reference would keep reporting the discarded history
        self._app = app

    def can_undo(self):
        return bool(self._app._undo_stack)

    def can_redo(self):
        return bool(self._app._redo_stack)

    def undo(self):
        return self._app._undo()

    def redo(self):
        return self._app._redo()

    @staticmethod
    def _top(names):
        # translated here, exactly as nbapp.UndoHistory._label_at does, so the
        # name reads in the interface language and not in English
        if not names or not names[-1]:
            return None
        # translate, THEN trim the ellipsis, exactly as UndoHistory does
        return _t(names[-1]).rstrip(" …")

    def undo_label(self):
        return self._top(self._app._undo_names)

    def redo_label(self):
        return self._top(self._app._redo_names)


class Layer:
    def __init__(self, name, w, h, fill_white=False):
        self.name = name
        self.visible = True
        self.opacity = 100
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        if fill_white:
            # OPERATOR_SOURCE on an untouched surface: a flat opaque white,
            # no blending involved.
            cr = cairo.Context(self.surface)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            self.surface.flush()


class Illustrator(nbapp.AppWindow):
    app_name = "Illustrator"
    menus = ("File", "Edit", "View", "Image", "Layer")

    def __init__(self):
        super().__init__()
        # Set before anything can arm a source or touch a widget: every
        # deferred callback below reads this to decide whether the window it
        # belongs to is still there. Each owned source keeps its id here so it
        # can be cancelled — a token alone stops the WRONG state from being
        # applied, but not a stale closure from running against dead widgets.
        self._closed = False
        self._recentre_src = 0     # pending zoom recentre idle (0 = none)
        self._fit_src = 0          # pending first fit-to-window idle
        self._chip_restore_src = 0  # pending flash auto-restore timeout

        self._install_css()
        self._build_checker_pattern()

        self.cw, self.ch = DEFAULT_CW, DEFAULT_CH
        self.tool = "pencil"
        self._prev_tool = "pencil"   # what the Eyedropper returns to
        self.size = 1
        self.zoom = 8
        self.grid = True
        self.fill_shapes = False
        self.sym_x = False        # mirror painting left/right
        self.sym_y = False        # mirror painting top/bottom
        self.color = "#000000"
        self.layers = [Layer(_t("Background"), self.cw, self.ch, fill_white=True)]
        self.active = 0
        self.next_id = 2
        self._drawing = False
        self._start = None
        self._last = None
        # (brush size, erase override) from the last sample of the live stroke.
        # (None, None) is the mouse case: chosen size, tool decides erasing.
        self._pen_last = (None, None)
        self._shift = False
        self._preview = None      # (tool, a, b) while a shape is being dragged
        self._preview_rect = None  # what of _scratch currently holds preview
        self._scratch = None      # preview pixels, exactly as they will commit
        self._cursor = None
        self._fitted = False      # has the first fit-to-window run yet
        self._dirty = False       # True once the canvas differs from the last save
        self._path = None         # current PNG file (File > Save writes here)
        self._undo_stack = []     # history frames (see _apply_frame), newest last
        self._redo_stack = []
        # what each frame would take back, for the Edit menu ("Undo Delete
        # Layer"). Kept beside the stacks rather than inside a frame, which is
        # unpacked positionally in several places. None = an unnamed edit (a
        # brush stroke), which reads as the bare "Undo".
        self._undo_names = []
        self._redo_names = []
        self.history = StackHistory(self)
        self._pending = None      # pixels held while an edit is in progress
        self._stroke_track = None  # union of everything the live gesture touched
        self._saveprompt_layer = None
        self._recent = self._load_recent()   # colours reached for before
        self._chip_state = "empty"   # save chip: empty | saved | unsaved
        self._saved_time = ""
        self._flash_token = 0        # guards transient _flash_save auto-restores

        # Guard both exit routes (Esc and the red logo dot both call
        # self.close(), which emits delete-event): when there are unsaved
        # changes, offer Save / Discard / Cancel before the window is destroyed.
        self.connect("delete-event", self._on_delete)
        # Separate from the guard above: delete-event decides WHETHER to close,
        # destroy is the close actually happening (including the routes that
        # bypass the guard — the prompt's Discard/Save, File ▸ Close, Shut
        # Down). Teardown of the deferred sources belongs on this one.
        self.connect("destroy", self._on_destroy)

        self._tool_btns = {}

        # --- workspace: dock + canvas mat + layers panel ---
        work = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        work.set_vexpand(True)
        # The dock is what sets this window's MINIMUM HEIGHT — it is the
        # tallest of the three columns — so it scrolls rather than pushing the
        # window past the panel it has to live on. Measured at 1024x740: the
        # dock wants 605px in English but 665px in Chinese, because CJK line
        # metrics are taller, and 763px total is 23px more than the shortest
        # laptop screen this OS supports. That clipped the bottom of the dock
        # in Chinese, Japanese and Korean, and the same arithmetic breaks every
        # language once the accessibility large-text setting is on. Scrolling
        # is the only fix that holds for text sizes nobody has measured yet.
        # Horizontal policy NEVER, so the column keeps its full width and only
        # the vertical bar can ever appear.
        self._dock_box = self._dock()
        self.dock_scroll = Gtk.ScrolledWindow()
        self.dock_scroll.set_policy(Gtk.PolicyType.NEVER,
                                    Gtk.PolicyType.AUTOMATIC)
        self.dock_scroll.set_propagate_natural_width(True)
        self.dock_scroll.get_style_context().add_class("dockscroll")
        self.dock_scroll.add(self._dock_box)
        work.pack_start(self.dock_scroll, False, False, 0)

        # The canvas is a fixed-size document shown at an integer zoom. With
        # room it sits centred in the papertone field; on a smaller real panel
        # (1366x768, 1024x740, ...) or at a high zoom the field SCROLLS instead
        # of clipping the canvas off the edges.
        self.mat = Gtk.ScrolledWindow()
        self.mat.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.mat.get_style_context().add_class("mat")
        self.mat.set_hexpand(True)
        self.mat.set_vexpand(True)
        # CRITICAL: a GtkScrolledWindow installs a capture-phase pan / kinetic-
        # scroll gesture that intercepts a pointer drag BEFORE it reaches the
        # canvas — so dragging to draw would also pan the viewport and the
        # canvas would visibly move out from under the stroke. Disable both so a
        # drag on the canvas ONLY paints; the scrollbars still scroll.
        self.mat.set_kinetic_scrolling(False)
        self.mat.set_capture_button_press(False)
        self.mat.connect("size-allocate", self._on_mat_allocate)

        canvas_wrap = Gtk.EventBox()
        self.canvas_wrap = canvas_wrap
        canvas_wrap.get_style_context().add_class("canvasfield")
        canvas_wrap.connect("draw", self._draw_canvas_field)

        canvas_holder = Gtk.Box()
        canvas_holder.get_style_context().add_class("canvasframe")
        canvas_holder.set_halign(Gtk.Align.CENTER)
        canvas_holder.set_valign(Gtk.Align.CENTER)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(int(math.ceil(self.cw * self.zoom)),
                                     int(math.ceil(self.ch * self.zoom)))
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.canvas.connect("draw", self._on_draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("scroll-event", self._on_scroll)
        self.canvas.connect("leave-notify-event", self._on_leave)
        self.canvas.connect("realize", self._on_canvas_realize)
        canvas_holder.add(self.canvas)
        canvas_wrap.add(canvas_holder)
        # The frame owns the margin around the document. A press there must be
        # able to begin a gesture before the pointer crosses onto the drawing
        # area; canvas events stop propagation, so normal presses are not seen
        # twice here.
        canvas_wrap.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK)
        canvas_wrap.connect("button-press-event", self._on_press)
        canvas_wrap.connect("button-release-event", self._on_release)
        canvas_wrap.connect("motion-notify-event", self._on_motion)
        self.mat.add(canvas_wrap)   # ScrolledWindow auto-wraps it in a Viewport
        work.pack_start(self.mat, True, True, 0)

        self.panel = self._layers_panel()
        work.pack_start(self.panel, False, False, 0)
        self.content.pack_start(work, True, True, 0)

        # --- status bar ---
        self.content.pack_start(self._statusbar(), False, False, 0)
        self._new_scratch()
        self._sync_controls()
        self._refresh_status()

    def _on_canvas_realize(self, w):
        win = w.get_window()
        # coalesce motion: GDK compresses queued motion events so a fast drag
        # produces one segment per frame, not one per raw device sample
        win.set_event_compression(True)
        try:
            win.set_cursor(Gdk.Cursor.new_from_name(
                w.get_display(), "crosshair"))
        except Exception:
            pass     # a cursor name the X server lacks is not worth a crash

    # ---------------- small shared pieces ----------------
    def _group(self, caption, spacing=6):
        """A captioned column. _set_dim greys a whole group by class, so the
        caption is reached through CSS descent and needs no handle here."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        col.pack_start(self._caption(caption), False, False, 0)
        return col

    def _caption(self, text):
        lbl = Gtk.Label(label=_t(text).upper(), xalign=0)
        lbl.get_style_context().add_class("caption")
        return lbl

    def _hsep(self):
        """A hairline rule between the dock's three sections."""
        s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        s.get_style_context().add_class("hsep")
        return s

    def _mark_btn(self, kind, tip, cb, label=None):
        """A button carrying one of this app's own cairo-drawn marks, and
        optionally a word beside it. The shipped face has no glyph for a minus
        sign or a mirror arrow, and a missing glyph is an invisible button on
        real hardware."""
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("stepbtn")
        b.set_tooltip_text(tip)
        da = Gtk.DrawingArea()
        da.set_size_request(15, 15)
        da._kind = kind
        da.connect("draw", self._draw_mark)
        box = Gtk.Box(spacing=6)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.add(da)
        if label:
            # a mark that names itself: Outline / Filled are a CHOICE, and two
            # unlabelled boxes would be one more pair to decode
            lbl = Gtk.Label(label=_t(label))
            lbl.get_style_context().add_class("marklabel")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            box.add(lbl)
            b.get_style_context().add_class("wide")
        b.add(box)
        b.connect("clicked", cb)
        return b

    def _draw_mark(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return False
        # A mark is painted, not styled, so a dimmed group's CSS never reaches
        # it: the Outline and Filled boxes stayed full ink beside their own
        # greyed-out words. Effective sensitivity is what the group sets.
        cr.set_source_rgb(*_rgb("#1A1916" if area.is_sensitive() else "#B3AD9E"))
        cr.set_line_width(1.6)
        k = area._kind
        cx, cy = round(w / 2.0) - 0.5, round(h / 2.0) - 0.5
        if k in ("minus", "plus"):
            cr.move_to(2, cy)
            cr.line_to(w - 2, cy)
            if k == "plus":
                cr.move_to(cx, 2)
                cr.line_to(cx, h - 2)
            cr.stroke()
        elif k == "fit":
            # a picture sitting inside its frame
            cr.rectangle(1.5, 1.5, w - 3, h - 3)
            cr.stroke()
            cr.rectangle(cx - 2.5, cy - 2.5, 6, 6)
            cr.fill()
        elif k in ("outline", "filled"):
            # the two ways a shape can come out, drawn as the thing itself: a
            # hollow box and a solid one. The word beside it says which; the
            # mark is what makes the pair readable at a glance.
            cr.set_line_width(1.4)
            cr.rectangle(2.2, 3.2, w - 4.4, h - 6.4)
            cr.fill() if k == "filled" else cr.stroke()
        elif k in ("symx", "symy"):
            # a dashed axis with a solid arrowhead facing away on either side:
            # two shapes reflected across a line is what the button does
            cr.save()
            cr.set_line_width(1)
            cr.set_dash([2, 2])
            if k == "symx":
                cr.move_to(cx, 0)
                cr.line_to(cx, h)
            else:
                cr.move_to(0, cy)
                cr.line_to(w, cy)
            cr.stroke()
            cr.restore()
            for s in (-1, 1):
                if k == "symx":
                    x0 = cx + s * 2
                    cr.move_to(x0, cy - 4)
                    cr.line_to(x0 + s * 4, cy)
                    cr.line_to(x0, cy + 4)
                else:
                    y0 = cy + s * 2
                    cr.move_to(cx - 4, y0)
                    cr.line_to(cx, y0 + s * 4)
                    cr.line_to(cx + 4, y0)
                cr.close_path()
                cr.fill()
        return False

    # ---------------- left dock ----------------
    def _dock(self):
        """The whole of "what a stroke will look like", in one column, in the
        order it is decided: which tool, how it is set up, what colour."""
        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        dock.get_style_context().add_class("dock")
        dock.set_size_request(DOCK_W, -1)

        dock.pack_start(self._toolbox(), False, False, 0)
        dock.pack_start(self._hsep(), False, False, 0)
        dock.pack_start(self._tool_settings(), False, False, 0)
        dock.pack_start(self._hsep(), False, False, 0)
        dock.pack_start(self._colour_section(), False, False, 0)
        return dock

    def _toolbox(self):
        """The eight tools, NAMED, two to a row.

        They used to be eight 26 px icon buttons in one strip. Three of the
        eight are the same diagonal implement at that size — pencil, brush and
        the eyedropper — so which button did what was a hover-one-at-a-time
        exercise, and picking the eyedropper by mistake meant the next click
        changed the colour instead of drawing. The name is the fix; the icon is
        now the fast recognition mark rather than the only evidence."""
        box = self._group("Tools", spacing=8)
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(4)
        grid.set_column_homogeneous(True)
        for i, (tid, name) in enumerate(TOOLS):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            # the tooltip carries the hint and the single-key shortcut, so the
            # keyboard route is discoverable from the button that does the same
            b.set_tooltip_text("%s  (%s)" % (_t(TOOL_HINTS.get(tid, name)),
                                             TOOL_KEYS[tid]))
            b.get_style_context().add_class("toolbtn")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            img = nbicons.image(tid, 17, "#6E695E")
            inner.pack_start(img, False, False, 0)
            lbl = Gtk.Label(label=_t(name), xalign=0)
            lbl.get_style_context().add_class("toolname")
            # a long translation ("Cuentagotas", "Пипетка") shortens rather
            # than widening the dock and squeezing the canvas
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            inner.pack_start(lbl, True, True, 0)
            b.add(inner)
            b._img = img
            b._tid = tid
            b.connect("clicked", self._pick_tool, tid)
            self._tool_btns[tid] = b
            grid.attach(b, i % 2, i // 2, 1, 1)
        box.pack_start(grid, False, False, 0)
        return box

    def _tool_settings(self):
        """Brush size, shape fill, mirror — the three things that change what
        the CURRENT tool puts down.

        Each group stays put and dims when the active tool cannot use it, so
        the dock never reflows under the pointer and a dimmed group is itself
        the answer to "why did the size not matter?". Shape fill in particular
        was Image > Fill Shapes / Outline Shapes — a menu item whose label
        inverted, so the only way to learn whether the next rectangle would be
        solid was to open a menu and read the label backwards."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # --- brush size ---
        self.size_grp = self._group("Brush size")
        srow = Gtk.Box(spacing=4)
        srow.pack_start(self._mark_btn("minus", _t("Smaller brush  ([)"),
                                       lambda *_: self._step_size(-1)),
                        False, False, 0)
        self.size_lbl = Gtk.Label(label="")
        self.size_lbl.get_style_context().add_class("numfield")
        self.size_lbl.set_size_request(60, 30)
        srow.pack_start(self.size_lbl, True, True, 0)
        srow.pack_start(self._mark_btn("plus", _t("Larger brush  (])"),
                                       lambda *_: self._step_size(1)),
                        False, False, 0)
        self.size_grp.pack_start(srow, False, False, 0)

        # the tips themselves, at their relative sizes and in the CURRENT
        # shape: the ramp is the size shortcut and the tip preview at once
        self.ramp_area = Gtk.DrawingArea()
        self.ramp_area.set_size_request(-1, self.RAMP_H)
        self.ramp_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.ramp_area.set_has_tooltip(True)
        self.ramp_area.connect("draw", self._draw_ramp)
        self.ramp_area.connect("button-press-event", self._on_ramp_press)
        self.ramp_area.connect("query-tooltip", self._ramp_tooltip)
        self.size_grp.pack_start(self.ramp_area, False, False, 0)
        col.pack_start(self.size_grp, False, False, 0)

        # --- shape fill / mirror, side by side ---
        pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.shape_grp = self._group("Shapes")
        frow = Gtk.Box(spacing=4)
        self.outline_btn = self._mark_btn(
            "outline", _t("Draw shapes as an outline"),
            lambda *_: self._set_fill_shapes(False), label="Outline")
        self.filled_btn = self._mark_btn(
            "filled", _t("Draw shapes filled in"),
            lambda *_: self._set_fill_shapes(True), label="Filled")
        frow.pack_start(self.outline_btn, True, True, 0)
        frow.pack_start(self.filled_btn, True, True, 0)
        self.shape_grp.pack_start(frow, False, False, 0)
        pair.pack_start(self.shape_grp, True, True, 0)

        self.mirror_grp = self._group("Mirror")
        mrow = Gtk.Box(spacing=4)
        self.symx_btn = self._mark_btn("symx", _t("Mirror left and right"),
                                       lambda *_: self._toggle_sym("x"))
        self.symy_btn = self._mark_btn("symy", _t("Mirror top and bottom"),
                                       lambda *_: self._toggle_sym("y"))
        mrow.pack_start(self.symx_btn, False, False, 0)
        mrow.pack_start(self.symy_btn, False, False, 0)
        self.mirror_grp.pack_start(mrow, False, False, 0)
        pair.pack_start(self.mirror_grp, False, False, 0)
        col.pack_start(pair, False, False, 0)
        return col

    def _colour_section(self):
        """The active colour, where the colours it comes from are.

        The well used to sit at the far right of the ribbon and the palette
        feeding it at the far left of the dock, about a thousand pixels apart —
        and clicking the well silently switched the active tool to the
        eyedropper, so a click on the colour swatch stopped the pencil drawing
        and made the next canvas click steal a colour instead. It opens the
        mixer now, which is what a click on a colour swatch means everywhere
        else; the eyedropper is a named tool with its own key."""
        box = self._group("Colour", spacing=8)

        wellrow = Gtk.Box(spacing=10)
        wellrow.set_valign(Gtk.Align.CENTER)
        self.chip = Gtk.DrawingArea()
        self.chip.set_size_request(40, 30)
        self.chip.get_style_context().add_class("chip")
        self.chip.set_tooltip_text(_t("Mix a colour"))
        self.chip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.chip.connect("draw", self._draw_chip)
        self.chip.connect("button-press-event", self._on_chip_press)
        wellrow.pack_start(self.chip, False, False, 0)
        # the colour has a NAME, from the same vocabulary the 112 swatches
        # hover with, so the well says which colour it is holding
        self.color_lbl = Gtk.Label(label="", xalign=0)
        self.color_lbl.get_style_context().add_class("colorname")
        self.color_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        wellrow.pack_start(self.color_lbl, True, True, 0)
        box.pack_start(wellrow, False, False, 0)

        mix = Gtk.Button(label=_t("Mix Colour…"))
        mix.set_relief(Gtk.ReliefStyle.NONE)
        mix.get_style_context().add_class("custombtn")
        mix.set_tooltip_text(_t("Mix a colour"))
        mix.connect("clicked", self._open_color_chooser)
        box.pack_start(mix, False, False, 0)

        # ONE DrawingArea for all 112 swatches, not 112 buttons: the grid is
        # painted in a single draw handler, a click is arithmetic, and the
        # hover name comes from query-tooltip. 112 widgets each with their own
        # GdkWindow would cost more to build and repaint than the whole canvas.
        self.pal_area = Gtk.DrawingArea()
        self.pal_area.set_size_request(self._pal_w(), self._pal_h())
        self.pal_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.pal_area.set_has_tooltip(True)
        self.pal_area.connect("draw", self._draw_palette)
        self.pal_area.connect("button-press-event", self._on_palette_press)
        self.pal_area.connect("query-tooltip", self._palette_tooltip)
        box.pack_start(self.pal_area, False, False, 0)

        # A caption over an empty strip reads as something that failed to load,
        # so the recent row appears only once there is a colour in it.
        self.recent_cap = self._caption("Recent")
        self.recent_cap.set_no_show_all(True)
        box.pack_start(self.recent_cap, False, False, 0)
        self.recent_area = Gtk.DrawingArea()
        self.recent_area.set_size_request(self._pal_w(), self.SW)
        self.recent_area.set_no_show_all(True)
        self.recent_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.recent_area.set_has_tooltip(True)
        self.recent_area.connect("draw", self._draw_recent)
        self.recent_area.connect("button-press-event", self._on_recent_press)
        self.recent_area.connect("query-tooltip", self._recent_tooltip)
        box.pack_start(self.recent_area, False, False, 0)
        self._sync_recent()
        return box

    # ---- the brush-size ramp ----
    RAMP_H = 30

    def _ramp_cells(self):
        """(x, w) of each ramp cell, from the strip's live width."""
        w = max(1, self.ramp_area.get_allocated_width())
        n = len(SIZE_RAMP)
        step = w / float(n)
        return [(i * step, step) for i in range(n)]

    def _draw_ramp(self, area, cr):
        """Five tips in one framed strip, so it reads as a single segmented
        control in the same idiom as the number field above it rather than as
        five dots adrift in the dock."""
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return False
        dim = not area.is_sensitive()
        cr.set_source_rgb(*_rgb("#F4F2EC" if dim else "#FCFBF8"))
        cr.rectangle(0, 0, w, h)
        cr.fill()
        square = self._brush_shape() == "square"
        cells = self._ramp_cells()
        for i, (x, cw) in enumerate(cells):
            n = SIZE_RAMP[i]
            sel = (n == self.size)
            if sel:
                cr.set_source_rgb(*_rgb("#EFEBE0" if dim else "#EAE3D2"))
                cr.rectangle(x, 0, cw, h)
                cr.fill()
            # the biggest tip nearly fills its cell and the rest are drawn in
            # proportion, so the strip reads small-to-large at a glance
            d = max(3.0, min(cw, h) * 0.62 * (n / float(SIZE_RAMP[-1])) ** 0.58)
            cx, cy = x + cw / 2.0, h / 2.0
            if dim:
                cr.set_source_rgb(*_rgb("#C9C4B6"))
            else:
                cr.set_source_rgb(*_rgb("#1A1916" if sel else "#6E695E"))
            if square:
                cr.rectangle(round(cx - d / 2.0), round(cy - d / 2.0),
                             round(d), round(d))
            else:
                cr.arc(cx, cy, d / 2.0, 0, 2 * math.pi)
            cr.fill()
        cr.set_line_width(1)
        cr.set_source_rgb(*_rgb("#D7D2C5" if dim else "#C9C4B6"))
        for x, _cw in cells[1:]:
            cr.move_to(round(x) + 0.5, 3)
            cr.line_to(round(x) + 0.5, h - 3)
        cr.stroke()
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        return False

    def _ramp_at(self, x, y):
        if y < 0 or y >= self.RAMP_H:
            return None
        for i, (cx, cw) in enumerate(self._ramp_cells()):
            if cx <= x < cx + cw:
                return SIZE_RAMP[i]
        return None

    def _on_ramp_press(self, _w, ev):
        n = self._ramp_at(ev.x, ev.y)
        if n is not None:
            self._set_size(n)
        return True

    def _ramp_tooltip(self, _w, x, y, _kb, tip):
        n = self._ramp_at(x, y)
        if n is None:
            return False
        tip.set_text(_t("%d px") % n)
        return True

    def _sync_recent(self):
        on = bool(self._recent)
        for w in (self.recent_cap, self.recent_area):
            w.set_visible(on)
        if on:
            self.recent_area.queue_draw()

    # swatch cell geometry, shared by the painter and the hit test
    SW = 13
    GAP = 1
    BAND_GAP = 4

    def _pal_w(self):
        return PAL_COLS * (self.SW + self.GAP) - self.GAP

    def _row_top(self, row):
        y = row * (self.SW + self.GAP)
        for band in PAL_BANDS:
            if row >= band:
                y += self.BAND_GAP
        return y

    def _pal_h(self):
        return self._row_top(PAL_ROWS - 1) + self.SW

    def _cell_rect(self, index):
        row, col = divmod(index, PAL_COLS)
        return (col * (self.SW + self.GAP), self._row_top(row), self.SW, self.SW)

    def _cell_at(self, x, y):
        col = int(x) // (self.SW + self.GAP)
        if col < 0 or col >= PAL_COLS or int(x) % (self.SW + self.GAP) >= self.SW:
            return None
        for row in range(PAL_ROWS):
            top = self._row_top(row)
            if top <= y < top + self.SW:
                i = row * PAL_COLS + col
                return i if i < len(PALETTE) else None
        return None

    def _paint_cell(self, cr, x, y, hex_, selected):
        cr.set_source_rgb(*_rgb(hex_))
        cr.rectangle(x, y, self.SW, self.SW)
        cr.fill()
        if selected:
            cr.set_source_rgb(*_rgb("#FCFBF8"))
            cr.set_line_width(2)
            cr.rectangle(x + 1.5, y + 1.5, self.SW - 3, self.SW - 3)
            cr.stroke()
            cr.set_source_rgb(*_rgb("#C8341E"))
            cr.set_line_width(1)
            cr.rectangle(x + 0.5, y + 0.5, self.SW - 1, self.SW - 1)
            cr.stroke()
        else:
            cr.set_source_rgb(*_rgb("#C9C4B6"))
            cr.set_line_width(1)
            cr.rectangle(x + 0.5, y + 0.5, self.SW - 1, self.SW - 1)
            cr.stroke()

    def _draw_palette(self, _area, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        for i, hex_ in enumerate(PALETTE):
            x, y, _w, _h = self._cell_rect(i)
            self._paint_cell(cr, x, y, hex_, hex_.upper() == self.color.upper())
        return False

    def _on_palette_press(self, _w, ev):
        i = self._cell_at(ev.x, ev.y)
        if i is not None:
            self._pick_color(None, PALETTE[i])
        return True

    def _palette_tooltip(self, _w, x, y, _kb, tip):
        i = self._cell_at(x, y)
        if i is None:
            return False
        tip.set_text(palette_name(i))
        return True

    def _recent_slot(self, x, y):
        if y < 0 or y >= self.SW:
            return None
        col = int(x) // (self.SW + self.GAP)
        if col < 0 or col >= len(self._recent):
            return None
        if int(x) % (self.SW + self.GAP) >= self.SW:
            return None
        return col

    def _draw_recent(self, _area, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        for i, hex_ in enumerate(self._recent[:RECENT_MAX]):
            self._paint_cell(cr, i * (self.SW + self.GAP), 0, hex_,
                             hex_.upper() == self.color.upper())
        return False

    def _on_recent_press(self, _w, ev):
        i = self._recent_slot(ev.x, ev.y)
        if i is not None:
            self._pick_color(None, self._recent[i])
        return True

    def _recent_tooltip(self, _w, x, y, _kb, tip):
        i = self._recent_slot(x, y)
        if i is None:
            return False
        tip.set_text(mix_name(self._recent[i]))
        return True

    # ---------------- layers panel ----------------
    def _layers_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class("lpanel")
        panel.set_size_request(PANEL_W, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("lhead")
        title = Gtk.Label(label=_t("Layers").upper(), xalign=0)
        title.get_style_context().add_class("ltitle")
        head.pack_start(title, True, True, 0)

        # Raise / lower the active layer. Without these, whatever order the user
        # happened to draw in is the order they are stuck with — a sky painted
        # last can never go behind the house, which is most of what layers are
        # for.
        self.up_btn = Gtk.Button()
        self.up_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.up_btn.get_style_context().add_class("liconbtn")
        self.up_btn.set_tooltip_text(_t("Bring layer forward"))
        self.up_btn.add(nbicons.image("up", 15, "#1A1916"))
        self.up_btn.connect("clicked", lambda *_: self._move_layer(1))
        head.pack_start(self.up_btn, False, False, 0)

        self.down_btn = Gtk.Button()
        self.down_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.down_btn.get_style_context().add_class("liconbtn")
        self.down_btn.set_tooltip_text(_t("Send layer back"))
        self.down_btn.add(nbicons.image("down", 15, "#1A1916"))
        self.down_btn.connect("clicked", lambda *_: self._move_layer(-1))
        head.pack_start(self.down_btn, False, False, 0)

        add = Gtk.Button()
        add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("liconbtn")
        add.set_tooltip_text(_t("New layer"))
        add.add(nbicons.image("plus", 15, "#1A1916"))
        add.connect("clicked", lambda *_: self._add_layer())
        head.pack_start(add, False, False, 0)

        self.del_btn = Gtk.Button()
        self.del_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.del_btn.get_style_context().add_class("liconbtn")
        self.del_btn.set_tooltip_text(_t("Delete layer"))
        self.del_btn.add(nbicons.image("trash", 15, "#1A1916"))
        self.del_btn.connect("clicked", lambda *_: self._delete_layer())
        head.pack_start(self.del_btn, False, False, 0)
        panel.pack_start(head, False, False, 0)

        self.layer_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.layer_list.get_style_context().add_class("llist")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.layer_list)
        panel.pack_start(scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        foot.get_style_context().add_class("lfoot")
        oprow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        opcap = Gtk.Label(label=_t("Opacity").upper(), xalign=0)
        opcap.get_style_context().add_class("caption")
        oprow.pack_start(opcap, True, True, 0)
        self.op_val = Gtk.Label(label="100%", xalign=1)
        self.op_val.get_style_context().add_class("caption")
        oprow.pack_start(self.op_val, False, False, 0)
        foot.pack_start(oprow, False, False, 0)

        self.op_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.op_scale.set_draw_value(False)
        self.op_scale.set_value(100)
        self.op_scale.get_style_context().add_class("opacity")
        self._op_handler = self.op_scale.connect("value-changed", self._on_opacity)
        foot.pack_start(self.op_scale, False, False, 0)
        panel.pack_start(foot, False, False, 0)

        self._rebuild_layers()
        return panel

    def _rebuild_layers(self, arriving=None, departing=None):
        """Rebuild the layer column, animating only the changed identity.

        ``arriving`` is a newly-added Layer. ``departing`` is ``(layer, index)``
        captured before deletion; its inert row is included at its old position
        only long enough to close. Every surviving row is packed plainly, so a
        one-row edit never restages the rest of the column.
        """
        for ch in self.layer_list.get_children():
            self.layer_list.remove(ch)
        # idx -> that row's opacity label, so a live opacity drag can update the
        # number in place without rebuilding this whole widget tree per tick
        self._op_labels = {}
        opening = []
        closing = []
        display = [(ly, idx, idx, True)
                   for idx, ly in enumerate(self.layers)]
        if departing is not None:
            gone, old_idx = departing
            # Deletion shifts every higher model index down by one. Restore
            # those rows' pre-delete display keys so the ghost occupies the
            # exact slot it is departing from.
            display = [(ly, idx, key + (key >= old_idx), interactive)
                       for ly, idx, key, interactive in display]
            display.append((gone, old_idx, old_idx, False))
        # top layer first; the saved index restores a deleted row's old place.
        display.sort(key=lambda item: item[2], reverse=True)
        for ly, idx, _display_key, interactive in display:
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("lrow")
            if interactive and idx == self.active:
                row.get_style_context().add_class("active")
            if interactive:
                row.connect("clicked", self._select_layer, idx)
            else:
                row.set_sensitive(False)

            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            eye = Gtk.Button()
            eye.set_relief(Gtk.ReliefStyle.NONE)
            eye.get_style_context().add_class("eyebtn")
            # Name the state it will move TO, so the button says what pressing
            # it does rather than what is currently true.
            eye.set_tooltip_text(_t("Hide this layer") if ly.visible
                                 else _t("Show this layer"))
            col = "#1A1916" if ly.visible else "#9A9484"
            try:
                eye.add(nbicons.image("eye" if ly.visible else "eyeoff", 15, col))
            except GLib.Error:
                eye.add(Gtk.Image())
            if interactive:
                eye.connect("clicked", self._toggle_visible, idx)
            else:
                eye.set_sensitive(False)
            inner.pack_start(eye, False, False, 0)

            name = Gtk.Label(label=ly.name, xalign=0)
            name.get_style_context().add_class("lname")
            if not ly.visible:
                # the struck-through eye was the only sign, and at 15px it is
                # a small one for a layer that is contributing nothing
                name.get_style_context().add_class("hidden")
            # the panel is a fixed column; a long name must ellipsize rather
            # than widen it and squeeze the canvas
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(14)
            inner.pack_start(name, True, True, 0)

            op = Gtk.Label(label="%d%%" % ly.opacity, xalign=1)
            op.get_style_context().add_class("lopacity")
            inner.pack_start(op, False, False, 0)
            if interactive:
                self._op_labels[idx] = op
            row.add(inner)
            changed = ly is arriving or not interactive
            if changed:
                try:
                    rev = Gtk.Revealer()
                    rev.set_reveal_child(not interactive)
                    rev.add(row)
                    self.layer_list.pack_start(rev, False, False, 0)
                    (opening if interactive else closing).append(rev)
                    continue
                except Exception:                                 # noqa: BLE001
                    pass
            # Motion is never a condition of content existing.
            if interactive:
                self.layer_list.pack_start(row, False, False, 0)

        # A single row above 400 px of nothing says the panel is empty rather
        # than that the document has one layer. One line of what the + does,
        # dropped as soon as there is a second layer to see the effect on.
        if len(self.layers) == 1:
            hint = Gtk.Label(
                label=_t("Add a layer to draw over the Background without "
                         "changing it."),
                xalign=0)
            hint.get_style_context().add_class("lempty")
            hint.set_line_wrap(True)
            hint.set_max_width_chars(24)
            self.layer_list.pack_start(hint, False, False, 0)

        # the bottom layer is the one the document always keeps, so it cannot be
        # deleted or sent further back; the top one has nowhere forward to go
        for btn, on in ((self.del_btn, self.active != 0),
                        (self.down_btn, self.active > 0),
                        (self.up_btn, self.active < len(self.layers) - 1)):
            btn.set_sensitive(on)
            sc = btn.get_style_context()
            if on:
                sc.remove_class("disabled")
            else:
                sc.add_class("disabled")
        self.del_btn.set_tooltip_text(
            _t("Delete layer") if self.active != 0 else
            _t("The Background layer cannot be deleted."))
        self.down_btn.set_tooltip_text(
            _t("Send layer back") if self.active > 0 else
            _t("This layer is already at the back."))
        self.up_btn.set_tooltip_text(
            _t("Bring layer forward") if self.active < len(self.layers) - 1 else
            _t("This layer is already at the front."))

        ly = self.layers[self.active]
        # Block the value-changed handler while syncing the slider to the active
        # layer: otherwise selecting a layer (or an opacity drag, which rebuilds
        # this list per tick) re-enters _on_opacity and rebuilds the list twice.
        self.op_scale.handler_block(self._op_handler)
        self.op_scale.set_value(ly.opacity)
        self.op_scale.handler_unblock(self._op_handler)
        self.op_val.set_text("%d%%" % ly.opacity)
        self.layer_list.show_all()

        # nbmotion-inventory: content.illustrator
        # Only the identity added/removed moves. Gtk.Revealer owns allocation
        # settling in C; policy-still reaches the exact visible/hidden state
        # synchronously. A primitive failure is forced to the same end state.
        for rev in opening:
            try:
                nbtransitions.reveal(
                    rev, True, direction=nbtransitions.SLIDE_DOWN,
                    duration=nbtransitions.SURFACE_IN)
            except Exception:                                     # noqa: BLE001
                try:
                    rev.set_reveal_child(True)
                except Exception:                                 # noqa: BLE001
                    pass
        for rev in closing:
            try:
                nbtransitions.reveal(
                    rev, False, direction=nbtransitions.SLIDE_UP,
                    duration=nbtransitions.SURFACE_OUT)
            except Exception:                                     # noqa: BLE001
                try:
                    rev.set_reveal_child(False)
                except Exception:                                 # noqa: BLE001
                    pass

    # ---------------- status bar ----------------
    def _statusbar(self):
        """Left: what the current tool does. Middle: where the pointer is.
        Right: the document's size, the zoom stepper, the save state.

        Zoom lives here rather than in a band across the top because the
        status bar was already printing the percentage — the app showed the
        zoom level twice and made it adjustable in neither place without
        crossing the window. The readout IS the control now."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bar.get_style_context().add_class("statusbar")
        # The tool hint is the widest thing here and the least costly to clip,
        # so it ellipsizes instead of pushing the zoom stepper off the end of
        # a 1024 px panel.
        self.st_tool = Gtk.Label(xalign=0)
        self.st_tool.get_style_context().add_class("stlabel")
        self.st_tool.set_ellipsize(Pango.EllipsizeMode.END)
        bar.pack_start(self.st_tool, False, False, 0)
        self.st_pos = Gtk.Label(xalign=0.5)
        self.st_pos.get_style_context().add_class("stlabel")
        bar.set_center_widget(self.st_pos)

        # Save state at the far right, beside the document's own facts, the
        # way Writer does it.
        self.save_lbl = Gtk.Label()
        self.save_lbl.get_style_context().add_class("savestate")
        self._render_chip()
        bar.pack_end(self.save_lbl, False, False, 0)

        zrow = Gtk.Box(spacing=3)
        zrow.set_valign(Gtk.Align.CENTER)
        # The same minus / plus marks the brush stepper uses, not the two
        # magnifier icons: at 15px a magnifying glass with a plus in it and one
        # with a minus in it are the same picture, and which button zoomed in
        # was a guess. The per-cent readout between them says which stepper
        # this is.
        zrow.pack_start(self._mark_btn("minus", _t("Zoom out  (-)"),
                                       lambda *_: self._step_zoom(-1)),
                        False, False, 0)
        self.zoom_lbl = Gtk.Label(label="", xalign=0.5)
        self.zoom_lbl.get_style_context().add_class("stlabel")
        self.zoom_lbl.set_size_request(46, -1)
        zrow.pack_start(self.zoom_lbl, False, False, 0)
        zrow.pack_start(self._mark_btn("plus", _t("Zoom in  (+)"),
                                       lambda *_: self._step_zoom(1)),
                        False, False, 0)
        zrow.pack_start(self._mark_btn("fit", _t("Fit to window  (0)"),
                                       lambda *_: self._zoom_fit()),
                        False, False, 0)
        bar.pack_end(zrow, False, False, 0)

        self.st_size = Gtk.Label(xalign=1)
        self.st_size.get_style_context().add_class("stlabel")
        bar.pack_end(self.st_size, False, False, 0)
        return bar

    def _refresh_status(self):
        """Cheap: this runs on every motion event. Only the labels that can
        change per pixel are touched; the zoom readout is driven by
        _sync_controls, which runs when the zoom actually changes."""
        ly = self.layers[self.active]
        if not ly.visible:
            # A hidden active layer swallows every stroke, which is the app's
            # most confusing state. Say so where the tool hint goes, rather
            # than only flashing it after a click has already done nothing.
            self.st_tool.set_text(
                _t('"%s" is hidden — strokes will not show') % ly.name)
        else:
            self.st_tool.set_text("%s — %s" % (
                _t(TOOL_NAMES.get(self.tool, "")),
                _t(TOOL_HINTS.get(self.tool, ""))))
        # While a shape is being dragged the useful number is its SIZE, not the
        # corner the pointer sits on: a 12x8 rectangle is what is being drawn,
        # and nothing on screen was saying so.
        if self._preview is not None:
            _tl, a, b = self._preview
            self.st_pos.set_text(self._dims(abs(b[0] - a[0]) + 1,
                                            abs(b[1] - a[1]) + 1))
        elif self._cursor:
            self.st_pos.set_text("%d, %d" % self._cursor)
        else:
            self.st_pos.set_text("")
        # The layer COUNT is gone — the Layers panel is open beside this bar
        # listing them — and so is the zoom, now that the stepper to the left
        # of this label carries it instead of printing it a second time.
        # one key, no leading space: " px" on its own is a PADDED catalog key,
        # which never matches and silently leaves the unit in English
        self.st_size.set_text(_t("%s px") % self._dims(self.cw, self.ch))

    @staticmethod
    def _dims(w, h):
        """W x H, isolated LTR. In a right-to-left interface two numbers either
        side of a neutral separator are reordered, and a 320x180 canvas was
        offering itself as 180x320."""
        return "\u2066%d × %d\u2069" % (w, h)

    # ---------------- chrome painting ----------------
    def _draw_canvas_field(self, area, cr):
        # CSS normally supplies this colour, but a resized native child window
        # can be exposed before style painting reaches its newly allocated
        # strip. An explicit full allocation paint makes zoom transitions
        # deterministic and prevents the display server's black clear colour
        # from flashing or remaining around a reduced document.
        paint_field(cr, area.get_allocated_width(), area.get_allocated_height())
        return False

    def _draw_chip(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return                       # not yet allocated — nothing to paint
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.rectangle(0, 0, w, h)
        cr.set_source_rgb(*_rgb(self.color))
        cr.fill()
        # Inset the 1px frame by half a pixel so it lands on the pixel grid and
        # renders as a crisp hairline instead of a clipped half-pixel at the edge.
        cr.set_source_rgb(*_rgb("#C9C4B6"))
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()

    def _build_checker_pattern(self):
        """The transparency checkerboard, once, as a repeating cairo pattern.
        _on_draw fills the damaged region with one native pattern paint instead
        of looping hundreds of Python-level tile fills on every repaint (the
        real, GPU-less framebuffer has nothing to hide that cost behind)."""
        t = 8
        tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, t * 2, t * 2)
        tc = cairo.Context(tile)
        tc.set_source_rgb(*_rgb("#F8F7F2"))
        tc.paint()
        tc.set_source_rgb(*_rgb("#EAE3D2"))
        tc.rectangle(t, 0, t, t)   # top-right
        tc.rectangle(0, t, t, t)   # bottom-left
        tc.fill()
        tile.flush()
        pat = cairo.SurfacePattern(tile)
        pat.set_extend(cairo.EXTEND_REPEAT)
        self._bg_tile = tile          # keep the tile surface alive for the pattern
        self._bg_pattern = pat

    def _on_draw(self, area, cr):
        # Repaint only the exposed region; cairo clips every op below to the
        # damaged rect, so a motion event never repaints the whole canvas.
        z = self.zoom
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.set_source(self._bg_pattern)
        cr.paint()
        cr.save()
        cr.scale(z, z)
        for ly in self.layers:
            if not ly.visible:
                continue
            cr.set_source_surface(ly.surface, 0, 0)
            # NEAREST or the zoom smears the artwork into a blur, which is the
            # one thing a pixel editor's zoom must never do.
            cr.get_source().set_filter(
                cairo.FILTER_NEAREST if z >= 1 else cairo.FILTER_BILINEAR)
            cr.paint_with_alpha(ly.opacity / 100.0)
        # the live shape guide is real pixels in a scratch surface, so what is
        # previewed is exactly what commits
        if self._preview is not None and self._scratch is not None:
            cr.set_source_surface(self._scratch, 0, 0)
            cr.get_source().set_filter(
                cairo.FILTER_NEAREST if z >= 1 else cairo.FILTER_BILINEAR)
            cr.paint()
        cr.restore()
        if self.grid and z >= GRID_FROM:
            self._draw_grid(cr, z)
        self._draw_brush_cursor(cr, z)
        return False

    def _cursor_rect(self):
        """The image-pixel box the pointer is currently over, brush included,
        as (x, y, w, h) — or None when the pointer is off the canvas.

        Shared by the painter and the damage code so the outline is erased
        from exactly where it was drawn."""
        if self._cursor is None:
            return None
        cx, cy = self._cursor
        if self.tool not in SIZE_TOOLS:
            return (cx, cy, 1, 1)      # fill and eyedropper act on one pixel
        runs = brush_runs(self.size, self._brush_shape())
        dy0 = min(r[0] for r in runs)
        dy1 = max(r[0] for r in runs)
        dx0 = min(r[1] for r in runs)
        dx1 = max(r[2] for r in runs)
        return (cx + dx0, cy + dy0, dx1 - dx0 + 1, dy1 - dy0 + 1)

    def _draw_brush_cursor(self, cr, z):
        """Outline the pixels the next click would change.

        A crosshair says where the pointer is; it does not say how much of the
        artwork a 12 px brush is about to cover, and at 100% zoom a size-8 tip
        landed nowhere near where it looked like it would. The footprint is
        drawn from the SAME brush_runs the stamp uses, so the outline and the
        paint can never disagree.

        Suppressed while a shape is being dragged: the shape preview already
        shows what will commit, and a second box around the pointer competes
        with it."""
        if self._cursor is None or self._preview is not None:
            return
        runs = (brush_runs(self.size, self._brush_shape())
                if self.tool in SIZE_TOOLS else ((0, 0, 0),))
        cx, cy = self._cursor
        cr.save()
        # A light wash first, then a dark hairline on top of it, so the
        # footprint stays visible over artwork of any colour.
        for dy, dx0, dx1 in runs:
            cr.rectangle((cx + dx0) * z, (cy + dy) * z, (dx1 - dx0 + 1) * z, z)
        cr.set_source_rgba(1, 1, 1, 0.34)
        cr.fill()
        # ...and the hairline traces the OUTSIDE only. Stroking the same
        # rectangles would rule a line between every pair of rows, so a round
        # 12 px tip came out as a stack of bars rather than a disc.
        pts = self._brush_outline(runs)
        if pts:
            x0, y0 = pts[0]
            cr.move_to((cx + x0) * z + 0.5, (cy + y0) * z + 0.5)
            for x, y in pts[1:]:
                cr.line_to((cx + x) * z + 0.5, (cy + y) * z + 0.5)
            cr.close_path()
            cr.set_line_width(1)
            cr.set_source_rgba(0.10, 0.10, 0.09, 0.62)
            cr.stroke()
        cr.restore()

    @staticmethod
    def _brush_outline(runs):
        """The boundary of a brush footprint, as one closed polygon in image
        pixels relative to the cursor.

        Every brush this app has is ROW-CONVEX — brush_runs emits exactly one
        run per row, for a full box or for the box's inscribed disc — so the
        boundary is the left edges walked down and the right edges walked back
        up. Returns () for anything that does not fit that shape, and the
        caller falls back to leaving the outline off rather than drawing a
        wrong one."""
        if not runs:
            return ()
        rows = sorted(runs)
        for a, b in zip(rows, rows[1:]):
            if b[0] != a[0] + 1:
                return ()            # a gap between rows: not row-convex
        left, right = [], []
        for dy, dx0, _dx1 in rows:                 # down the left edges
            left.append((dx0, dy))
            left.append((dx0, dy + 1))
        for dy, _dx0, dx1 in reversed(rows):       # back up the right edges
            right.append((dx1 + 1, dy + 1))
            right.append((dx1 + 1, dy))
        # The rows are walked in reverse HERE rather than reversing the
        # finished list: that flipped each row's own pair of points too, and
        # the outline came back as a zigzag crossing itself down one side.
        return tuple(left) + tuple(right)

    def _dmg_cursor(self, rect=None):
        """Repaint where the brush outline is, or was. `rect` is a previous
        footprint to clear as well as the current one."""
        for r in (rect, self._cursor_rect()):
            if r is not None:
                self._dmg(r)

    def _draw_grid(self, cr, z):
        """One hairline per image-pixel boundary, and a heavier one every 8, so
        a block can be counted at a glance. Only the lines crossing the damaged
        region are drawn."""
        x0, y0, x1, y1 = cr.clip_extents()
        cr.set_line_width(1)
        # lighter while the blocks are still small, or the mesh competes with
        # the artwork it is there to help count
        fine = 0.12 if z < 12 else 0.17
        for step, colour in ((1, (0.10, 0.10, 0.09, fine)),
                             (8, (0.10, 0.10, 0.09, 0.34))):
            if step == 8 and z < 12:
                continue
            cr.set_source_rgba(*colour)
            i = max(0, int(x0 // z) // step * step)
            while i * z <= x1 and i <= self.cw:
                if step == 1 and i % 8 == 0 and z >= 12:
                    i += step
                    continue
                cr.move_to(i * z + 0.5, max(0, y0))
                cr.line_to(i * z + 0.5, min(self.ch * z, y1))
                i += step
            j = max(0, int(y0 // z) // step * step)
            while j * z <= y1 and j <= self.ch:
                if step == 1 and j % 8 == 0 and z >= 12:
                    j += step
                    continue
                cr.move_to(max(0, x0), j * z + 0.5)
                cr.line_to(min(self.cw * z, x1), j * z + 0.5)
                j += step
            cr.stroke()

    # ---------------- the pixel engine ----------------
    def _brush_shape(self):
        return "round" if self.tool == "brush" else "square"

    def _mirror_points(self, pts):
        """`pts` plus its reflections under the active mirror axes."""
        if not (self.sym_x or self.sym_y):
            return pts
        out = list(pts)
        if self.sym_x:
            w = self.cw - 1
            out += [(w - x, y) for (x, y) in pts]
        if self.sym_y:
            h = self.ch - 1
            out += [(x, h - y) for (x, y) in list(out)]
        return out

    def _mirror_spans(self, spans):
        if not (self.sym_x or self.sym_y):
            return spans
        out = list(spans)
        if self.sym_x:
            w = self.cw - 1
            out += [(y, w - xb, w - xa) for (y, xa, xb) in spans]
        if self.sym_y:
            h = self.ch - 1
            out += [(h - y, xa, xb) for (y, xa, xb) in list(out)]
        return out

    def _stamp_on(self, surf, pts, px, size=None):
        """Stamp the brush at every point of `pts` into `surf`.

        The slice assignment REPLACES those bytes: a painted pixel is exactly
        `px`, never a blend of `px` and what was under it. That is what makes
        the edges hard."""
        if not pts:
            return
        surf.flush()
        data = surf.get_data()
        stride = surf.get_stride()
        w, h = self.cw, self.ch
        for dy, dx0, dx1 in brush_runs(self.size if size is None else size,
                                       self._brush_shape()):
            for cx, cy in pts:
                y = cy + dy
                if y < 0 or y >= h:
                    continue
                x0, x1 = cx + dx0, cx + dx1
                if x1 < 0 or x0 >= w:
                    continue
                if x0 < 0:
                    x0 = 0
                if x1 >= w:
                    x1 = w - 1
                i = y * stride + x0 * 4
                n = x1 - x0 + 1
                data[i:i + 4 * n] = px * n
        surf.mark_dirty()

    def _spans_on(self, surf, spans, px):
        """Write whole horizontal runs — filled shapes, in one slice per row."""
        if not spans:
            return
        surf.flush()
        data = surf.get_data()
        stride = surf.get_stride()
        w, h = self.cw, self.ch
        for y, xa, xb in spans:
            if y < 0 or y >= h or xb < 0 or xa >= w:
                continue
            xa = max(0, xa)
            xb = min(w - 1, xb)
            i = y * stride + xa * 4
            n = xb - xa + 1
            data[i:i + 4 * n] = px * n
        surf.mark_dirty()

    def _paint_ops(self, surf, pts, spans, erase=False, size=None):
        px = CLEAR4 if erase else px4(self.color)
        self._stamp_on(surf, pts, px, size)
        self._spans_on(surf, spans, px)

    def _ops_bbox(self, pts, spans):
        """The (x, y, w, h) those ops can have touched, brush size included."""
        xs, ys = [], []
        pad = max(1, int(self.size))
        for x, y in pts:
            xs.append(x)
            ys.append(y)
        for y, xa, xb in spans:
            xs.append(xa)
            xs.append(xb)
            ys.append(y)
        if not xs:
            return None
        return (min(xs) - pad, min(ys) - pad,
                max(xs) - min(xs) + 1 + 2 * pad,
                max(ys) - min(ys) + 1 + 2 * pad)

    def _shape_ops(self, tool, a, b):
        """(points, spans) for one shape, in image pixels. Nothing here knows
        about cairo."""
        if tool == "line":
            return _line_points(a[0], a[1], b[0], b[1]), []
        lx0, lx1 = sorted((a[0], b[0]))
        ly0, ly1 = sorted((a[1], b[1]))
        if tool == "rect":
            if self.fill_shapes:
                return [], [(y, lx0, lx1) for y in range(ly0, ly1 + 1)]
            pts = (_line_points(lx0, ly0, lx1, ly0)
                   + _line_points(lx1, ly0, lx1, ly1)
                   + _line_points(lx1, ly1, lx0, ly1)
                   + _line_points(lx0, ly1, lx0, ly0))
            return pts, []
        spans = _ellipse_spans(lx0, ly0, lx1, ly1)
        if self.fill_shapes:
            return [], spans
        return _ellipse_outline(spans), []

    # ---------------- interaction ----------------
    def _pick_tool(self, _b, tid):
        # what the Eyedropper hands the tool back to when it has sampled
        if tid == "picker" and self.tool != "picker":
            self._prev_tool = self.tool
        was = self._cursor_rect()
        self.tool = tid
        self._sync_controls()
        self._refresh_status()
        # the pointer outline is the brush's footprint, and the brush just
        # changed shape (round tip, square tip, or a single pixel)
        self._dmg_cursor(was)

    def _step_size(self, delta):
        self._set_size(self.size + delta)

    def _set_size(self, n):
        """The one way the brush size changes, so the ramp, the readout and the
        outline under the pointer all move together."""
        n = max(SIZE_MIN, min(SIZE_MAX, int(n)))
        if n == self.size:
            return
        was = self._cursor_rect()
        self.size = n
        self._sync_controls()
        self._dmg_cursor(was)

    def _pick_color(self, _b, c):
        self.color = c.upper()
        self._remember(self.color)
        self._sync_controls()

    def _set_fill_shapes(self, on):
        """Shapes come out solid or hollow. Two buttons that show which is
        active, rather than one menu item that named the OTHER one."""
        if on == self.fill_shapes:
            return
        self.fill_shapes = on
        self._sync_controls()

    @staticmethod
    def _set_dim(widget, on):
        """Dim or undim a group: the CSS class carries the whole look, and
        set_sensitive stops its buttons responding to a click that could not
        have done anything anyway."""
        sc = widget.get_style_context()
        if on:
            sc.remove_class("dim")
        else:
            sc.add_class("dim")
        widget.set_sensitive(on)

    def _sync_controls(self):
        """Put every control in the dock and the status bar in step with the
        app's state. One place, so the selected tool, the highlighted swatch,
        the tip ramp and the readouts can never disagree."""
        for tid, b in self._tool_btns.items():
            sc = b.get_style_context()
            if tid == self.tool:
                sc.add_class("sel")
            else:
                sc.remove_class("sel")
            try:
                nbicons.set_image(
                    b._img, tid, 17,
                    "#FCFBF8" if tid == self.tool else "#6E695E")
            except GLib.Error:
                pass
        for btn, on in ((self.symx_btn, self.sym_x),
                        (self.symy_btn, self.sym_y),
                        (self.outline_btn, not self.fill_shapes),
                        (self.filled_btn, self.fill_shapes)):
            sc = btn.get_style_context()
            if on:
                sc.add_class("sel")
            else:
                sc.remove_class("sel")
        # A setting the current tool cannot reach goes quiet instead of sitting
        # there looking live: the flood fill takes no brush and no mirror, the
        # eyedropper takes nothing at all, and only the three shape tools care
        # whether a shape is filled. Nothing moves — the groups keep their
        # places, so the dock never reflows under the pointer.
        self._set_dim(self.size_grp, self.tool in SIZE_TOOLS)
        self._set_dim(self.shape_grp, self.tool in SHAPE_TOOLS)
        self._set_dim(self.mirror_grp, self.tool in MIRROR_TOOLS)
        self.size_lbl.set_text(_t("%d px") % self.size)
        pct = float(self.zoom * 100)
        self.zoom_lbl.set_text(("%d%%" if pct.is_integer() else "%.1f%%") % pct)
        self.color_lbl.set_text(mix_name(self.color))
        self.ramp_area.queue_draw()
        self.chip.queue_draw()
        self.pal_area.queue_draw()
        self.recent_area.queue_draw()

    def _toggle_sym(self, axis):
        if axis == "x":
            self.sym_x = not self.sym_x
        else:
            self.sym_y = not self.sym_y
        self._sync_controls()

    # ---- zoom ----
    def _set_zoom(self, z):
        if self._closed:
            return
        z = max(ZOOM_MIN, min(ZOOM_MAX, float(z)))
        if z == self.zoom:
            return
        # keep whatever is in the middle of the viewport in the middle of it
        old = self.zoom
        ha = self.mat.get_hadjustment()
        va = self.mat.get_vadjustment()
        cx = (ha.get_value() + ha.get_page_size() / 2.0) / old
        cy = (va.get_value() + va.get_page_size() / 2.0) / old
        self.zoom = z
        self.canvas.set_size_request(int(math.ceil(self.cw * z)),
                                     int(math.ceil(self.ch * z)))
        self.canvas.queue_draw()
        self._sync_controls()
        self._refresh_status()

        def _recentre():
            # Give up ownership first: whatever happens below, this source is
            # already spent and must never be handed to source_remove again.
            self._recentre_src = 0
            if self._closed:
                return False       # the adjustments belong to a dead window
            ha.set_value(max(0, min(ha.get_upper() - ha.get_page_size(),
                                    cx * z - ha.get_page_size() / 2.0)))
            va.set_value(max(0, min(va.get_upper() - va.get_page_size(),
                                    cy * z - va.get_page_size() / 2.0)))
            return False

        # Only the NEWEST centre is worth applying. Held down, Ctrl+scroll or
        # the zoom buttons run _set_zoom several times before the main loop
        # goes idle once; without this the viewport visibly chased each stale
        # centre in turn over the following frames.
        self._cancel_source("_recentre_src")
        self._recentre_src = GLib.idle_add(_recentre)

    def _step_zoom(self, delta):
        steps = [s for s in ZOOM_STEPS]
        if self.zoom not in steps:
            steps = sorted(set(steps + [self.zoom]))
        i = steps.index(self.zoom)
        self._set_zoom(steps[max(0, min(len(steps) - 1, i + delta))])

    def _zoom_fit(self):
        # Reachable from the View menu and from a queued idle, either of which
        # can be delivered after the window has gone.
        if self._closed:
            return
        alloc = self.mat.get_allocation()
        aw = alloc.width - 12 if alloc.width > 20 else 0
        ah = alloc.height - 12 if alloc.height > 20 else 0
        if aw <= 0 or ah <= 0:
            return
        self._set_zoom(fit_zoom(self.cw, self.ch, aw, ah))

    def _on_mat_allocate(self, *_a):
        # The first real allocation is the first moment the window's size is
        # known, so that is when the document is fitted to it.
        if self._closed:
            return
        if not self._fitted:
            self._fitted = True
            # _fitted makes this a one-time arm, so there is never a second
            # source to coalesce with; the id is kept only so a close that
            # lands before the main loop goes idle can cancel it.
            self._fit_src = GLib.idle_add(self._fit_idle)

    def _fit_idle(self):
        self._fit_src = 0
        if self._closed:
            return False
        self._zoom_fit()
        return False

    def _on_scroll(self, _w, ev):
        if not (ev.state & Gdk.ModifierType.CONTROL_MASK):
            return False          # plain scroll still scrolls the mat
        if ev.direction == Gdk.ScrollDirection.UP:
            self._step_zoom(1)
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            self._step_zoom(-1)
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            if ev.delta_y < 0:
                self._step_zoom(1)
            elif ev.delta_y > 0:
                self._step_zoom(-1)
        return True

    def _toggle_grid(self):
        self.grid = not self.grid
        self.canvas.queue_draw()

    # ---- coordinates ----
    def _pos(self, ev, widget=None, clamp=False):
        """Event coordinate -> IMAGE PIXEL. The canvas widget is exactly
        cw*zoom by ch*zoom, so this is a floor division by the zoom and nothing
        else. Deliberately NOT clamped to the canvas: a brush half off the left
        edge has to paint the half that is on it, and clamping would bend the
        stroke back along the edge instead."""
        x, y = ev.x, ev.y
        if widget is not None and widget is not self.canvas:
            try:
                x, y = widget.translate_coordinates(self.canvas, x, y)
            except (TypeError, AttributeError):
                pass
        return view_pixel(x, y, self.zoom, self.cw, self.ch, clamp)

    def _pen(self, ev):
        """(effective brush size, erase-override) for one event.

        A size of None means "whatever the brush is set to, live" — the value
        the paint path already used before tablets existed. A mouse therefore
        returns (None, None) and every path below behaves exactly as it did,
        down to honouring a brush size changed mid-stroke.

        The device SOURCE is what gates this, not the presence of a pressure
        axis: a touchscreen reports pressure too, and scaling a fingertip by it
        would make touch strokes wander between widths for no reason the hand
        can see."""
        try:
            dev = ev.get_source_device()
            src = dev.get_source() if dev is not None else None
        except (AttributeError, TypeError):
            return None, None
        if src not in (Gdk.InputSource.PEN, Gdk.InputSource.ERASER):
            return None, None
        # The eraser end of a stylus erases whatever tool is selected — that is
        # what turning the pen over means. Only for the freehand tools: flipping
        # the pen should not silently turn "draw a rectangle" into something
        # else, so the shape tools keep their own behaviour.
        erase = None
        if src == Gdk.InputSource.ERASER and self.tool in ("pencil", "brush"):
            erase = True
        try:
            ok, p = ev.get_axis(Gdk.AxisUse.PRESSURE)
        except (AttributeError, TypeError):
            ok, p = False, None
        # A pen in range but not touching the surface reports 0.0; that is a
        # real reading, not a missing one, so only a MISSING axis falls back to
        # the chosen size.
        if not ok or p is None:
            return None, erase
        return pen_size(self.size, p), erase

    def _inside(self, p):
        return 0 <= p[0] < self.cw and 0 <= p[1] < self.ch

    def _dmg(self, rect):
        """Queue a repaint of an image-pixel rect, in widget coordinates."""
        if rect is None:
            self.canvas.queue_draw()
            return
        x, y, w, h = rect
        z = self.zoom
        x0, y0 = math.floor(x * z) - 1, math.floor(y * z) - 1
        x1, y1 = math.ceil((x + w) * z) + 1, math.ceil((y + h) * z) + 1
        self.canvas.queue_draw_area(int(x0), int(y0), int(x1 - x0),
                                    int(y1 - y0))

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return False
        self._shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        p = self._pos(ev, _w, clamp=(_w is not self.canvas))
        # The eyedropper samples the composited canvas, so it works no matter
        # which layer is active or whether it is hidden — handle it before the
        # active-layer visibility guard.
        if self.tool == "picker":
            if self._inside(p):
                self._pick_from_canvas(p)
            return True
        ly = self.layers[self.active]
        if not ly.visible:
            # Painting a hidden layer changes nothing on screen; say why the
            # click did nothing instead of silently swallowing it.
            self._flash_save(_t("Active layer is hidden"))
            return True
        self._drawing = True
        self._start = p
        self._last = p
        if self.tool == "fill":
            if self._inside(p) and self._flood_fill(ly, p):
                self._end_stroke()        # snapshots internally, only on a fill
            else:
                self._drawing = False     # nothing to fill — not an edit
        elif self.tool in ("pencil", "brush", "eraser"):
            self._begin_edit()        # hold pre-stroke pixels for Undo
            self._stroke_track = None
            self._pen_last = size, erase = self._pen(ev)
            self._stroke_seg(ly, p, p, size, erase)
        # line / rect / ellipse only touch the surface on release, so the Undo
        # snapshot is taken there — and only for a shape that was actually
        # dragged — so a stray single click never dirties the doc or wipes Redo.
        return True

    def _on_motion(self, _w, ev):
        p = self._pos(ev, _w, clamp=(self._drawing and _w is not self.canvas))
        was = self._cursor_rect()
        self._cursor = p if self._inside(p) else None
        self._refresh_status()
        # Erase the outline from where it was and paint it where it is. Both
        # rects are brush-sized, so this is a couple of hundred pixels per
        # motion event even at a high zoom — nothing like a full repaint.
        if self._cursor_rect() != was:
            self._dmg_cursor(was)
        if not self._drawing:
            return False
        self._shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        ly = self.layers[self.active]
        if self.tool in ("pencil", "brush", "eraser"):
            self._pen_last = size, erase = self._pen(ev)
            self._stroke_seg(ly, self._last, p, size, erase)
            self._last = p
        elif self.tool in ("line", "rect", "ellipse"):
            self._render_preview(self._start, self._constrain(self._start, p))
        return True

    def _constrain(self, a, b):
        if not self._shift:
            return b
        return _snap45(a, b) if self.tool == "line" else _square(a, b)

    def _on_release(self, _w, ev):
        if not self._drawing:
            return False
        p = self._pos(ev, _w, clamp=(_w is not self.canvas))
        ly = self.layers[self.active]
        if self.tool in ("line", "rect", "ellipse"):
            self._shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
            b = self._constrain(self._start, p)
            old = self._clear_preview()
            if p == self._start and self.tool != "line":
                # a click with no drag draws no box: keep it a true no-op so it
                # neither dirties the doc nor clears the Redo history
                self._drawing = False
                self._dmg(old)
                return True
            pts, spans = self._shape_ops(self.tool, self._start, b)
            pts = self._mirror_points(pts)
            spans = self._mirror_spans(spans)
            self._begin_edit()        # hold pre-shape pixels for Undo
            self._paint_ops(ly.surface, pts, spans)
            region = self._ops_bbox(pts, spans)
            if old is not None and region is not None:
                region = self._union_rect(region, old)
            self._end_stroke(region)
            return True
        # Freehand: paint the final segment up to the release point (event
        # compression can drop the last motion sample, so the stroke would
        # otherwise stop short), then finalize with a tight repaint.
        if p != self._last:
            # The RELEASE event's pressure is the lift — near zero by the time
            # the pen has left the surface. Using it here would taper the last
            # segment to a hairline that the hand never drew, and event
            # compression can make that segment long. Carry the last sample the
            # pen actually painted at instead.
            size, erase = self._pen_last
            self._stroke_seg(ly, self._last, p, size, erase)
            self._last = p
        self._end_stroke(self._stroke_track)
        return True

    def _on_leave(self, _w, _ev):
        # take the brush outline with the pointer, or it is left stranded on
        # the artwork after the hand has gone
        was = self._cursor_rect()
        self._cursor = None
        self._refresh_status()
        self._dmg_cursor(was)
        return False

    def _stroke_seg(self, ly, a, b, size=None, erase=None):
        pts = self._mirror_points(_line_points(a[0], a[1], b[0], b[1]))
        if erase is None:
            erase = (self.tool == "eraser")
        self._paint_ops(ly.surface, pts, [], erase=erase, size=size)
        region = self._ops_bbox(pts, [])
        # Grow the running record of everything this gesture has touched, so the
        # Undo frame committed at release covers the WHOLE stroke and not just
        # its last repainted segment.
        if region is not None:
            self._stroke_track = (region if self._stroke_track is None
                                  else self._union_rect(self._stroke_track,
                                                        region))
        self._dmg(region)

    # ---- live shape preview, in real pixels ----
    def _new_scratch(self):
        self._scratch = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                           self.cw, self.ch)
        self._preview_rect = None

    def _clear_preview(self):
        """Wipe the preview surface and return the rect it occupied."""
        old = self._preview_rect
        if old is not None and self._scratch is not None:
            cr = cairo.Context(self._scratch)
            cr.set_operator(cairo.OPERATOR_CLEAR)
            x, y, w, h = self._clamp_rect(old)
            cr.rectangle(x, y, w, h)
            cr.fill()
            self._scratch.flush()
        self._preview = None
        self._preview_rect = None
        return old

    def _render_preview(self, a, b):
        old = self._clear_preview()
        pts, spans = self._shape_ops(self.tool, a, b)
        pts = self._mirror_points(pts)
        spans = self._mirror_spans(spans)
        self._paint_ops(self._scratch, pts, spans)
        self._preview = (self.tool, a, b)
        self._preview_rect = self._ops_bbox(pts, spans)
        dmg = self._preview_rect
        if old is not None:
            dmg = old if dmg is None else self._union_rect(dmg, old)
        self._dmg(dmg)

    def _end_stroke(self, region=None):
        """Finalize a committed stroke / shape / fill: drop the drawing flag,
        repaint, bank the Undo frame and re-arm the Unsaved chip. `region` is a
        tight (x, y, w, h) box for a local edit so only the damaged rect
        recomposites; None means repaint everything (a flood fill can touch
        anywhere)."""
        self._drawing = False
        self._dmg(region)
        track, self._stroke_track = self._stroke_track, None
        self._commit_edit(track if track is not None else region)
        self._remember(self.color)
        self._mark_unsaved()

    # ---------------- undo / redo ----------------
    # The history is one stack of frames covering BOTH kinds of edit a drawing
    # can suffer, so every destructive action in the app is reversible:
    #
    #   ("px", layer, x, y, snapshot)     pixels — the rectangle an edit touched
    #   ("st", layers, active, cw, ch)    structure — the layer list AND the
    #                                     canvas size, so adding, deleting,
    #                                     reordering and RESIZING all undo
    #
    # A structural frame holds the old list, which still references a deleted
    # Layer object — that is what keeps its pixels alive to be restored. A
    # resize builds new Layer objects, so the frame's old ones still carry the
    # artwork at the old size. Frames name their layer by identity, never by
    # index, so history stays correct after layers are added or removed.
    def _clamp_rect(self, region):
        """`region` intersected with the canvas, as (x, y, w, h) of ints."""
        x, y, w, h = region
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1 = min(self.cw, int(x) + int(w))
        y1 = min(self.ch, int(y) + int(h))
        return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    def _crop_surface(self, surf, region):
        """An independent ARGB32 copy of just `region` of `surf`, or None when
        the region falls outside the canvas."""
        x, y, w, h = self._clamp_rect(region)
        if w <= 0 or h <= 0:
            return None
        surf.flush()
        cp = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(cp)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(surf, -x, -y)
        cr.paint()
        cp.flush()
        return cp

    @staticmethod
    def _blit(ly, x, y, snap):
        """Put `snap` back into layer `ly` at (x, y), replacing those pixels."""
        cr = cairo.Context(ly.surface)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(snap, x, y)
        cr.rectangle(x, y, snap.get_width(), snap.get_height())
        cr.fill()
        ly.surface.flush()
        ly.surface.mark_dirty()

    @staticmethod
    def _frame_surfaces(frame):
        """Every pixel surface a history frame keeps alive."""
        if frame[0] == "st":
            return [ly.surface for ly in frame[1]]
        return [frame[4]]

    def _history_bytes(self):
        """Pixel bytes the history keeps alive that the document does not.

        A surface still in self.layers is the live document and costs the
        history nothing, which is what makes Add / Delete / Move Layer nearly
        free: their frames hold the same surfaces the canvas is already showing.
        The ones that cost are Flip and Canvas Size, which build replacement
        layers and leave the originals owned by the history alone. Counted by
        identity so a surface held by several frames is charged once."""
        live = {id(ly.surface) for ly in self.layers}
        seen, total = set(), 0
        for frame in self._undo_stack:
            for surf in self._frame_surfaces(frame):
                if id(surf) in live or id(surf) in seen:
                    continue
                seen.add(id(surf))
                total += surf.get_stride() * surf.get_height()
        return total

    def _push(self, frame, name=None):
        """Add a frame to the history and drop the Redo trail (any fresh edit
        makes the redone future unreachable). Oldest frames fall off the front
        at UNDO_DEPTH, and again once the frames hold more than HISTORY_BYTES of
        pixels, so memory stays bounded in steps as well as in count.

        `name` is the menu wording of the action this frame takes back, shown
        in the Edit menu; None for an edit that needs no name (a stroke)."""
        self._undo_stack.append(frame)
        self._undo_names.append(name)
        # dropped first: the redo trail is unreachable after any fresh edit, and
        # it must not be charged for pixels that are about to be released
        self._redo_stack = []
        self._redo_names = []
        self._trim_history()

    def _trim_history(self):
        """Drop the oldest frames until the history fits both bounds.

        Called again by Flip and Canvas Size AFTER they swap self.layers: at
        _push time those ops have not replaced the document yet, so the frame
        they just pushed still holds the LIVE surfaces and is charged nothing.
        Trimming only at push left the history one whole frame over the
        ceiling — a full extra copy of the document."""
        while len(self._undo_stack) > UNDO_DEPTH:
            self._undo_stack.pop(0)
            self._undo_names.pop(0)
        # never drop the newest frame: one step of Undo always has to work
        while len(self._undo_stack) > 1 and self._history_bytes() > HISTORY_BYTES:
            self._undo_stack.pop(0)
            self._undo_names.pop(0)

    def _struct_frame(self):
        return ("st", list(self.layers), self.active, self.cw, self.ch)

    def _begin_edit(self):
        """Hold the active layer's current pixels while an edit is in progress.
        Kept whole only until _commit_edit crops it to the rectangle the edit
        actually touched, so nothing canvas-sized is retained per stroke."""
        ly = self.layers[self.active]
        self._pending = (ly, self._crop_surface(ly.surface,
                                                (0, 0, self.cw, self.ch)))

    def _commit_edit(self, region=None, name=None):
        """Turn the held pixels into a history frame. `region` is the (x, y, w,
        h) an edit touched — the frame keeps only that; None means the edit
        could have touched anywhere (a flood fill, Clear Layer) and the whole
        layer is kept."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        ly, before = pending
        if before is None:
            return
        if region is not None:
            x, y, w, h = self._clamp_rect(region)
            if w <= 0 or h <= 0:
                return           # the edit fell off the canvas — nothing to undo
            before = self._crop_surface(before, (x, y, w, h))
            if before is None:
                return
        else:
            x, y = 0, 0
        self._push(("px", ly, x, y, before), name)

    def _apply_frame(self, frame):
        """Apply one history frame and return the inverse frame for the other
        stack. None means the frame no longer applies (its layer is gone) and
        the caller should move on to the next one rather than appear to do
        nothing."""
        if frame[0] == "st":
            _kind, layers, active, cw, ch = frame
            inverse = self._struct_frame()
            self.layers = list(layers)
            self.active = max(0, min(len(self.layers) - 1, active))
            if (cw, ch) != (self.cw, self.ch):
                self.cw, self.ch = cw, ch
                self._sync_canvas_size()
            return inverse
        _kind, ly, x, y, snap = frame
        if ly not in self.layers:
            return None
        if (ly.surface.get_width(), ly.surface.get_height()) != (self.cw, self.ch):
            return None      # stale: this layer predates a canvas resize
        inverse = ("px", ly, x, y, self._crop_surface(
            ly.surface, (x, y, snap.get_width(), snap.get_height())))
        self._blit(ly, x, y, snap)
        self.active = self.layers.index(ly)
        return inverse

    def _step_history(self, take, give, take_names, give_names):
        """Move one frame from `take` to `give`, applying it. Shared by Undo and
        Redo — they are the same operation with the stacks swapped. The action
        names ride along in lockstep so the Edit menu keeps naming the right
        step after any number of undos."""
        while take:
            name = take_names.pop() if take_names else None
            inverse = self._apply_frame(take.pop())
            if inverse is None:
                continue                      # stale frame — try the next
            give.append(inverse)
            give_names.append(name)
            if len(give) > UNDO_DEPTH:
                give.pop(0)
                give_names.pop(0)
            self._rebuild_layers()
            self._refresh_status()
            self.canvas.queue_draw()
            self._mark_unsaved()
            return True
        return False

    def _undo(self):
        self._step_history(self._undo_stack, self._redo_stack,
                           self._undo_names, self._redo_names)

    def _redo(self):
        self._step_history(self._redo_stack, self._undo_stack,
                           self._redo_names, self._undo_names)

    @staticmethod
    def _union_rect(r1, r2):
        """Smallest (x, y, w, h) rect covering both r1 and r2."""
        x0 = min(r1[0], r2[0])
        y0 = min(r1[1], r2[1])
        x1 = max(r1[0] + r1[2], r2[0] + r2[2])
        y1 = max(r1[1] + r1[3], r2[1] + r2[3])
        return (x0, y0, x1 - x0, y1 - y0)

    # ---------------- fill / sample ----------------
    def _flood_fill(self, ly, p):
        # Scanline (span) flood fill: each stack entry seeds a whole horizontal
        # run, which is expanded left/right, written in one C-level slice, and
        # only the rows directly above and below are scanned for the next seeds.
        # Exact byte equality is the match test, which is what a pixel editor
        # wants — no tolerance, no feathering.
        surf = ly.surface
        surf.flush()
        stride = surf.get_stride()
        data = surf.get_data()
        px, py = p
        idx = py * stride + px * 4
        target = bytes(data[idx:idx + 4])
        newpx = px4(self.color)
        if target == newpx:
            return False   # nothing to do — don't dirty the doc or push a frame
        self._begin_edit()

        def _match(i):
            return data[i:i + 4] == target

        cw, ch = self.cw, self.ch
        stack = [(px, py)]
        while stack:
            x, y = stack.pop()
            row = y * stride
            if not _match(row + x * 4):
                continue
            x1 = x
            while x1 > 0 and _match(row + (x1 - 1) * 4):
                x1 -= 1
            x2 = x
            while x2 < cw - 1 and _match(row + (x2 + 1) * 4):
                x2 += 1
            i0 = row + x1 * 4
            data[i0:i0 + (x2 - x1 + 1) * 4] = newpx * (x2 - x1 + 1)
            for ny in (y - 1, y + 1):
                if ny < 0 or ny >= ch:
                    continue
                nrow = ny * stride
                xx = x1
                while xx <= x2:
                    if _match(nrow + xx * 4):
                        while xx <= x2 and _match(nrow + xx * 4):
                            xx += 1
                        stack.append((xx - 1, ny))
                    else:
                        xx += 1
        surf.mark_dirty()
        return True

    def _pick_from_canvas(self, p):
        tmp = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.cw, self.ch)
        cr = cairo.Context(tmp)
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        tmp.flush()
        stride = tmp.get_stride()
        data = tmp.get_data()
        i = p[1] * stride + p[0] * 4
        b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a == 0:
            return
        # cairo ARGB32 stores premultiplied alpha; un-premultiply (divide RGB by
        # alpha) so partly transparent pixels sample their true colour
        if a < 255:
            r = min(255, (r * 255 + a // 2) // a)
            g = min(255, (g * 255 + a // 2) // a)
            b = min(255, (b * 255 + a // 2) // a)
        self.color = "#%02X%02X%02X" % (r, g, b)
        self._remember(self.color)
        # Sampling is a detour, so it hands the tool back — but to whatever was
        # being drawn with, not always the Pencil. Taking a colour mid-stroke
        # with the 12 px Brush used to leave a 1 px Pencil in its place, and
        # the next drag came out as a hairline.
        self.tool = self._prev_tool if self._prev_tool in TOOL_NAMES else "pencil"
        self._sync_controls()
        self._refresh_status()
        self._dmg_cursor()

    # ---------------- colour history ----------------
    def _load_recent(self):
        """Colours reached for in earlier sessions, newest first. Never raises:
        a missing, empty or hand-mangled file just means no history yet."""
        got = None
        try:
            import json
            with open(CFG_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            got = data.get("recent")
            if got is None:
                got = data.get("mixes")   # what earlier versions wrote
        except Exception:
            return []
        out = []
        for c in (got if isinstance(got, list) else []):
            if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
                try:
                    _rgb255(c)
                except (ValueError, IndexError):
                    continue
                if c.upper() not in out:
                    out.append(c.upper())
        return out[:RECENT_MAX]

    def _remember(self, hex_):
        """Keep a colour the user reached for, newest first, and write it out so
        it is still there next time the app opens."""
        hex_ = hex_.upper()
        if self._recent[:1] == [hex_]:
            return                      # already newest — nothing to rewrite
        self._recent = [hex_] + [c for c in self._recent if c != hex_]
        del self._recent[RECENT_MAX:]
        try:
            self._sync_recent()
        except AttributeError:
            pass                        # called before the dock exists
        try:
            nbapp.atomic_write_json(CFG_FILE, {"recent": self._recent})
        except Exception:
            pass          # a colour history is never worth an error on screen

    def _on_chip_press(self, _w, _ev):
        # A click on the active-colour well opens the mixer, which is what a
        # click on a colour swatch means. It used to ARM THE EYEDROPPER: the
        # pencil silently stopped drawing and the next click on the artwork
        # took a colour instead of putting one down, with nothing on screen to
        # say the tool had changed. The eyedropper is a named button with a
        # key of its own; it does not need a trapdoor here as well.
        self._open_color_chooser()
        return True

    def _open_color_chooser(self, *_):
        """Mix any colour, beyond the palette and the eyedropper.

        A papertone card on the app's own prompt overlay rather than the stock
        Gtk.ColorChooserDialog, which is a separate window carrying the
        toolkit's own palette, rounded chips and 'Custom +' editor — a
        different product's look dropped into the middle of this one."""
        try:
            mix = {"rgb": list(_rgb255(self.color))}
        except (ValueError, IndexError):
            mix = {"rgb": [0, 0, 0]}

        well = Gtk.DrawingArea()
        well.set_size_request(-1, 44)

        def _draw_well(_w, cr):
            a = _w.get_allocation()
            r, g, b = mix["rgb"]
            cr.set_antialias(cairo.ANTIALIAS_NONE)
            cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0)
            cr.rectangle(0, 0, a.width, a.height)
            cr.fill()
            cr.set_source_rgb(*_rgb("#C9C4B6"))
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, a.width - 1, a.height - 1)
            cr.stroke()
            return False

        well.connect("draw", _draw_well)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.pack_start(well, False, False, 0)
        # The mixed colour NAMES itself as the sliders move, from the same
        # vocabulary the 112 swatches hover with. Three numbers from 0 to 255
        # are how the colour is built, not what it is.
        name_lbl = Gtk.Label(label=mix_name(self.color), xalign=0)
        name_lbl.get_style_context().add_class("colorname")
        body.pack_start(name_lbl, False, False, 0)

        sliders = []          # filled below; a recent chip drives them

        def _repaint():
            well.queue_draw()
            name_lbl.set_text(mix_name("#%02X%02X%02X" % tuple(mix["rgb"])))

        def _load_mix(hex_):
            """Put a previously used colour back on the sliders."""
            mix["rgb"] = list(_rgb255(hex_))
            for i, sc in enumerate(sliders):
                sc.set_value(mix["rgb"][i])
            _repaint()

        # Colours used before, newest first, so a blue found once never has to
        # be found again. Only shown when there is something to show.
        if self._recent:
            recent = Gtk.Box(spacing=6)
            cap = Gtk.Label(label=_t("Recent").upper(), xalign=0)
            cap.get_style_context().add_class("caption")
            body.pack_start(cap, False, False, 0)
            for hex_ in self._recent[:8]:
                b = Gtk.Button()
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.get_style_context().add_class("swatch")
                b.set_size_request(26, 26)
                # named like the palette swatches, never a hex code
                b.set_tooltip_text(mix_name(hex_))
                da = Gtk.DrawingArea()
                da.set_size_request(26, 26)
                da._col = hex_
                da.connect("draw", self._draw_one_swatch)
                b.add(da)
                b.connect("clicked", lambda _w, h=hex_: _load_mix(h))
                recent.pack_start(b, False, False, 0)
            body.pack_start(recent, False, False, 0)

        for idx, name in enumerate(("Red", "Green", "Blue")):
            row = Gtk.Box(spacing=12)
            lbl = Gtk.Label(label=_t(name), xalign=0)
            lbl.get_style_context().add_class("mixname")
            lbl.set_size_request(52, -1)
            row.pack_start(lbl, False, False, 0)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            sc.set_draw_value(False)
            sc.set_value(mix["rgb"][idx])
            sc.set_size_request(200, -1)
            sc.get_style_context().add_class("opacity")   # the app's one slider

            def _moved(scale, i=idx):
                mix["rgb"][i] = int(round(scale.get_value()))
                _repaint()

            sc.connect("value-changed", _moved)
            sliders.append(sc)
            row.pack_start(sc, True, True, 0)
            body.pack_start(row, False, False, 0)

        def _use():
            self._pick_color(None, "#%02X%02X%02X" % tuple(mix["rgb"]))
            self._refresh_status()

        self._overlay_prompt(
            _t("Mix a colour"),
            _t("Red, green and blue, 0 to 255."),
            [("Cancel", "ilpromptcancel", None),
             (_t("Use this colour"), "ilpromptok", _use)],
            content=body)

    def _draw_one_swatch(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.set_source_rgb(*_rgb(area._col))
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(*_rgb("#C9C4B6"))
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        return False

    # ---------------- layer ops ----------------
    def _select_layer(self, _b, idx):
        self.active = idx
        self._rebuild_layers()
        self._refresh_status()

    def _toggle_visible(self, btn, idx):
        self.layers[idx].visible = not self.layers[idx].visible
        self._rebuild_layers()
        self.canvas.queue_draw()
        self._mark_unsaved()  # visibility changes the saved PNG -> re-arm Unsaved

    def _add_layer(self):
        self._push(self._struct_frame(), "New Layer")
        ly = Layer(_t("Layer %d") % self.next_id, self.cw, self.ch)
        self.next_id += 1
        self.layers.append(ly)
        self.active = len(self.layers) - 1
        self._rebuild_layers(arriving=ly)
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()

    def _move_layer(self, delta):
        """Move the active layer one step up (+1, towards the front) or down
        (-1) the stack, keeping it selected. A structural history frame, so a
        reorder undoes like everything else."""
        i = self.active
        j = i + delta
        if j < 0 or j >= len(self.layers):
            return
        # named with the menu wording of the move that was made, so the Edit
        # menu reads "Undo Bring Forward" / "Undo Send Back"
        self._push(self._struct_frame(),
                   "Bring Forward" if delta > 0 else "Send Back")
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self.active = j
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()   # the stacking order changes the saved PNG

    def _delete_layer(self):
        # Deleting a layer is a history frame like any other edit, so it takes
        # one press of Ctrl+Z to get the layer and its artwork back.
        if not (0 < self.active < len(self.layers)):
            return
        self._push(self._struct_frame(), "Delete Layer")
        old_idx = self.active
        gone = self.layers[old_idx]
        name = gone.name
        del self.layers[self.active]
        self.active = max(0, self.active - 1)
        self._rebuild_layers(departing=(gone, old_idx))
        self._refresh_status()
        self.canvas.queue_draw()
        self._mark_unsaved()
        # after _mark_unsaved, which re-renders the chip and would otherwise
        # wipe this notice off it
        self._flash_save(_t('Deleted "%s" — press Ctrl+Z to bring it back')
                         % name)

    def _on_opacity(self, scale):
        v = int(scale.get_value())
        ly = self.layers[self.active]
        # Only a real opacity change is a doc edit; a drag that rounds to the
        # same integer (or the blocked set_value on layer select) is a no-op.
        if v == ly.opacity:
            return
        ly.opacity = v
        self.op_val.set_text("%d%%" % v)
        # Live opacity drags fire value-changed many times a second. Update just
        # the active row's number label in place rather than rebuilding the
        # whole layer list per tick.
        lbl = self._op_labels.get(self.active)
        if lbl is not None:
            lbl.set_text("%d%%" % v)
        self.canvas.queue_draw()
        self._mark_unsaved()  # opacity change alters the saved PNG

    def _show_all_layers(self):
        for ly in self.layers:
            ly.visible = True
        self._rebuild_layers()
        self.canvas.queue_draw()
        self._mark_unsaved()

    def _clear_active_layer(self):
        if not self.layers:
            return
        self._begin_edit()   # hold the layer's pixels so Clear Layer is undoable
        ly = self.layers[self.active]
        cr = cairo.Context(ly.surface)
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        ly.surface.flush()
        ly.surface.mark_dirty()
        # cleared everywhere — keep the whole layer, named for the Edit menu
        self._commit_edit(name="Clear Layer")
        self.canvas.queue_draw()
        self._mark_unsaved()

    # ---------------- image ops ----------------
    def _flip(self, horizontal):
        """Mirror every layer. Done by reversing whole 4-byte pixels in the
        buffer rather than by a cairo scale(-1), which would resample."""
        self._push(self._struct_frame(),
                   "Flip Horizontal" if horizontal else "Flip Vertical")
        old = self.layers
        new = []
        for ly in old:
            nl = Layer(ly.name, self.cw, self.ch)
            nl.visible, nl.opacity = ly.visible, ly.opacity
            ly.surface.flush()
            src = ly.surface.get_data()
            dst = nl.surface.get_data()
            ss, ds = ly.surface.get_stride(), nl.surface.get_stride()
            for y in range(self.ch):
                ty = (self.ch - 1 - y) if not horizontal else y
                if horizontal:
                    row = bytes(src[y * ss:y * ss + self.cw * 4])
                    flipped = b"".join(
                        row[i * 4:i * 4 + 4] for i in range(self.cw - 1, -1, -1))
                    dst[ty * ds:ty * ds + self.cw * 4] = flipped
                else:
                    dst[ty * ds:ty * ds + self.cw * 4] = \
                        bytes(src[y * ss:y * ss + self.cw * 4])
            nl.surface.mark_dirty()
            new.append(nl)
        self.layers = new
        # the frame pushed above now owns the old surfaces outright
        self._trim_history()
        self._rebuild_layers()
        self.canvas.queue_draw()
        self._mark_unsaved()

    def _resize_canvas(self, w, h):
        """Change the document's pixel size. Artwork keeps its position from the
        top-left corner: cropped where the canvas shrinks, transparent where it
        grows. No resampling — a pixel is never averaged with its neighbour."""
        w = max(MIN_DIM, min(MAX_DIM, int(w)))
        h = max(MIN_DIM, min(MAX_DIM, int(h)))
        if (w, h) == (self.cw, self.ch):
            return
        self._push(self._struct_frame(), "Canvas Size…")
        new = []
        for ly in self.layers:
            nl = Layer(ly.name, w, h)
            nl.visible, nl.opacity = ly.visible, ly.opacity
            cr = cairo.Context(nl.surface)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_surface(ly.surface, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.rectangle(0, 0, min(w, ly.surface.get_width()),
                         min(h, ly.surface.get_height()))
            cr.fill()
            nl.surface.flush()
            new.append(nl)
        self.layers = new
        self.cw, self.ch = w, h
        # the frame pushed above now owns the old surfaces outright
        self._trim_history()
        self._sync_canvas_size()
        self._rebuild_layers()
        self._mark_unsaved()

    def _sync_canvas_size(self):
        """Re-fit everything that is sized in image pixels after cw/ch change."""
        self._new_scratch()
        self._preview = None
        self.canvas.set_size_request(int(math.ceil(self.cw * self.zoom)),
                                     int(math.ceil(self.ch * self.zoom)))
        self.canvas.queue_draw()
        self._zoom_fit()
        self._refresh_status()

    def _canvas_size_prompt(self):
        """Pick a canvas size: a preset, or any width and height."""
        state = {"w": str(self.cw), "h": str(self.ch)}
        fields = {}

        # `state` holds what the user has typed, kept in step with the entries
        # by their own "changed" signal. _apply MUST read this and never the
        # widgets: the prompt's buttons dismiss the card BEFORE running their
        # callback, which destroys the entries inside it, and a destroyed
        # GtkEntry returns "" from get_text(). That is why Canvas Size did
        # nothing at all — every value read back empty, so the old code fell
        # back to the size the document already had and _resize_canvas returned
        # early on (w, h) == (cw, ch). The colour mixer above is right for the
        # same reason: it reads mix["rgb"], not its sliders.
        buttons = {}

        def _mark():
            """Ring the preset the entries currently hold, so the grid says
            where the document IS and not only where it could go."""
            for (w, h), btn in buttons.items():
                sc = btn.get_style_context()
                if (str(w), str(h)) == (state["w"].strip(), state["h"].strip()):
                    sc.add_class("sel")
                else:
                    sc.remove_class("sel")

        def _set(w, h):
            state["w"], state["h"] = str(w), str(h)
            fields["w"].set_text(str(w))
            fields["h"].set_text(str(h))
            _mark()

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(6)
        presets = ((16, 16), (32, 32), (48, 48), (64, 64),
                   (96, 96), (128, 128), (256, 256), (320, 180))
        for i, (w, h) in enumerate(presets):
            # Isolated LTR: in a right-to-left interface two numbers either
            # side of a neutral separator are reordered, and "320 x 180"
            # was offering the user a 180x320 canvas.
            b = Gtk.Button(label=self._dims(w, h))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("presetbtn")
            b.connect("clicked", lambda _b, w=w, h=h: _set(w, h))
            buttons[(w, h)] = b
            grid.attach(b, i % 4, i // 4, 1, 1)
        body.pack_start(grid, False, False, 0)

        for key, cap in (("w", "Width"), ("h", "Height")):
            row = Gtk.Box(spacing=12)
            lbl = Gtk.Label(label=_t(cap), xalign=0)
            lbl.get_style_context().add_class("mixname")
            lbl.set_size_request(58, -1)
            row.pack_start(lbl, False, False, 0)
            e = Gtk.Entry()
            e.set_text(str(state[key]))
            e.set_width_chars(6)
            e.set_max_length(4)
            e.get_style_context().add_class("sizeentry")
            # keep `state` current as it is typed — see _set above
            def _typed(ent, k=key):
                state[k] = ent.get_text()
                _mark()

            e.connect("changed", _typed)
            row.pack_start(e, False, False, 0)
            fields[key] = e
            body.pack_start(row, False, False, 0)
        _mark()

        def _apply():
            # A number outside the range, or not a number at all, is REPORTED.
            # It used to be quietly replaced — typing 2048 gave a 1024 canvas
            # and typing "big" gave the size it already had, both without a
            # word, so the dialog looked like it had ignored the request.
            raw = {k: str(state[k]).strip() for k in ("w", "h")}
            vals, bad = {}, []
            for k, cap in (("w", "Width"), ("h", "Height")):
                try:
                    n = int(raw[k])
                except ValueError:
                    bad.append(_t(cap))
                    continue
                if not (MIN_DIM <= n <= MAX_DIM):
                    bad.append(_t(cap))
                    continue
                vals[k] = n
            if bad:
                self._flash_save(
                    _t("%s must be a number from %d to %d")
                    % (" / ".join(bad), MIN_DIM, MAX_DIM))
                return
            self._resize_canvas(vals["w"], vals["h"])

        self._overlay_prompt(
            _t("Canvas size"),
            _t("Artwork keeps its position from the top-left corner. "
               "Width and height can be %d to %d pixels.")
            % (MIN_DIM, MAX_DIM),
            [("Cancel", "ilpromptcancel", None),
             (_t("Resize"), "ilpromptok", _apply)],
            content=body)

    # ---------------- file: New / Open / Save / Save As (PNG) ----------------
    def _flatten_surface(self):
        """Composite the visible layers (honoring per-layer opacity) into one
        ARGB32 surface. Transparency is preserved — the exported PNG matches
        exactly what the canvas shows through the checkerboard."""
        flat = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.cw, self.ch)
        cr = cairo.Context(flat)
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        flat.flush()
        return flat

    def _write_png(self, path):
        """Flatten the visible layers and write the PNG to `path`. Returns True
        on success; never raises so a bad path can't crash the app.

        Written beside the destination and moved into place, so a save that
        fails part-way (disk full, power loss) leaves the previous PNG at
        `path` untouched instead of replacing it with a half-written one.
        Re-saving over an earlier drawing is the normal way to use this, and
        that earlier file can be the only copy.

        The draft used to be `path + ".new"` — a name that can already belong
        to somebody. Saving "drawing.png" silently overwrote any real
        "drawing.png.new" sitting beside it, and a failed save then DELETED
        it: a file this app never opened and the person never named. The
        shared writer drafts under a unique temp name instead, so the only
        file at risk is the one being saved."""
        try:
            nbapp.atomic_write_via(
                path,
                lambda draft: self._flatten_surface().write_to_png(draft))
            return True
        except Exception:
            return False

    def _file_new(self):
        """Blank canvas at the current size. Confirms first when there is
        unsaved work to lose (as Writer / Novel do)."""
        self._confirm_discard(
            "Starting a new canvas will discard them.", self._do_file_new)

    def _do_file_new(self):
        self.layers = [Layer(_t("Background"), self.cw, self.ch,
                             fill_white=True)]
        self._reset_document()
        self._mark_empty()

    def _reset_document(self):
        self.active = 0
        self.next_id = 2
        self._undo_stack = []
        self._redo_stack = []
        self._undo_names = []
        self._redo_names = []
        self._pending = None
        self._stroke_track = None
        self._preview = None
        self._drawing = False
        self.op_scale.set_value(100)
        self._new_scratch()
        self.canvas.set_size_request(int(math.ceil(self.cw * self.zoom)),
                                     int(math.ceil(self.ch * self.zoom)))
        self._rebuild_layers()
        self._refresh_status()
        self.canvas.queue_draw()

    def _open_file(self, path):
        """Load a PNG as a new document, AT ITS OWN PIXEL SIZE.

        Nothing is scaled or centred: the canvas becomes the image's size, so
        save -> open is pixel-for-pixel identical and a 32x32 sprite comes back
        as a 32x32 sprite. The Background starts transparent (not white) so a
        PNG with an alpha channel keeps its transparency. An image larger than
        MAX_DIM is the one case that has to shrink, and it shrinks with
        FILTER_NEAREST — never a smoothing filter."""
        try:
            img = cairo.ImageSurface.create_from_png(path)
        except Exception:
            self._flash_save(_t("Could not open image"))
            return False
        iw, ih = img.get_width(), img.get_height()
        if iw <= 0 or ih <= 0:
            self._flash_save(_t("Could not open image"))
            return False
        scale = min(MAX_DIM / float(iw), MAX_DIM / float(ih), 1.0)
        self.cw = max(MIN_DIM, int(iw * scale))
        self.ch = max(MIN_DIM, int(ih * scale))
        base = Layer(_t("Background"), self.cw, self.ch)
        cr = cairo.Context(base.surface)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        if scale != 1.0:
            cr.scale(scale, scale)
        cr.set_source_surface(img, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        base.surface.flush()
        self.layers = [base]
        self._path = path
        self._reset_document()
        self._zoom_fit()
        self._mark_saved()
        return True

    def _file_open(self):
        """Open a PNG. Confirms first when there is unsaved work to lose, then
        shows the chooser under $NB_HOME/Pictures."""
        self._confirm_discard(
            "Opening another image will discard them.", self._do_file_open)

    def _do_file_open(self):
        path = self._choose_file(save=False)
        if path and os.path.isfile(path):
            self._open_file(path)

    def _file_save(self):
        """Write to the current file; prompt via Save As if there is none.
        Returns True once the PNG is on disk."""
        if not self._path:
            return self._file_save_as()
        if self._write_png(self._path):
            self._mark_saved()
            return True
        self._flash_save(_t("Could not save image"))
        return False

    def _file_save_as(self):
        """Pick a path, adopt it, and write the PNG there. Returns True on a
        successful write (False if the chooser was cancelled)."""
        path = self._choose_file(save=True)
        if not path:
            return False
        if not path.lower().endswith(".png"):
            path += ".png"          # the document is always a PNG on disk
        # Save As is a two-phase operation: the new filename becomes this
        # document's identity only after bytes reached it.  Assigning _path
        # first meant ENOSPC/read-only media abandoned the previous valid path
        # and bound the window to a file that did not exist; the next Ctrl+S
        # retried that failed destination instead of the document the person
        # still had open.
        if self._write_png(path):
            self._path = path
            self._mark_saved()
            return True
        self._flash_save(_t("Could not save image"))
        return False

    def _choose_file(self, save):
        """Finder-style in-app picker under $NB_HOME/Pictures; path or None."""
        try:
            os.makedirs(PICS_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.dirname(self._path) if self._path else PICS_DIR
        start = base if os.path.isdir(base) else PICS_DIR
        if save:
            suggested = (os.path.basename(self._path) if self._path
                         else "illustration.png")
            return nbpicker.save_file(self, title="Save Image As",
                                      start_dir=start, suggested_name=suggested,
                                      patterns=("*.png",), default_ext=".png")
        return nbpicker.open_file(self, title="Open Image",
                                  start_dir=start, patterns=("*.png",))

    def _flatten_pixbuf(self):
        """Composite the visible layers over a white matte and return a
        GdkPixbuf. Routes cairo -> PNG bytes -> PixbufLoader (the same safe path
        nbicons uses) rather than Gdk.pixbuf_get_from_surface, whose cairo
        foreign-type bridge isn't guaranteed on this build."""
        flat = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.cw, self.ch)
        cr = cairo.Context(flat)
        cr.set_source_rgb(1, 1, 1)   # white matte so transparency isn't black
        cr.paint()
        for ly in self.layers:
            if ly.visible:
                cr.set_source_surface(ly.surface, 0, 0)
                cr.paint_with_alpha(ly.opacity / 100.0)
        flat.flush()
        buf = io.BytesIO()
        flat.write_to_png(buf)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(buf.getvalue())
        loader.close()
        return loader.get_pixbuf()

    def _copy_image(self):
        """Copy the flattened canvas to the system clipboard as an image, so it
        can be pasted into another app."""
        try:
            pb = self._flatten_pixbuf()
            if pb is None:
                return
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_image(pb)
            clip.store()
        except Exception:
            pass

    def _render_chip(self):
        """Paint the save-state chip from _chip_state. One renderer so the dot
        colour and wording can never drift apart between the three states."""
        if self._chip_state == "saved":
            markup = ('<span foreground="#7FA98C">●</span>  '
                      '<span foreground="#6E695E">%s</span>'
                      % GLib.markup_escape_text(_t("Saved %s")
                                                % self._saved_time))
        elif self._chip_state == "unsaved":
            markup = ('<span foreground="#C8341E">●</span>  '
                      '<span foreground="#6E695E">%s</span>'
                      % GLib.markup_escape_text(_t("Unsaved changes")))
        else:
            markup = ('<span foreground="#9A9484">●</span>  '
                      '<span foreground="#6E695E">%s</span>'
                      % GLib.markup_escape_text(_t("Empty canvas")))
        try:
            self.save_lbl.set_markup(markup)
        except Exception:
            pass

    def _supersede_flash(self):
        """A durable chip state has just been established, so any transient
        flash is over: bump the token (the pending restore must not repaint an
        older state) AND drop the timer itself, since a callback that is
        guaranteed to do nothing is only a callback waiting to outlive the
        window."""
        self._flash_token += 1
        self._cancel_source("_chip_restore_src")

    def _mark_saved(self):
        """Green 'Saved HH:MM' chip — shown only once the PNG is on disk."""
        self._dirty = False
        self._chip_state = "saved"
        self._saved_time = time.strftime("%H:%M")
        self._supersede_flash()    # cancel any pending flash auto-restore
        self._render_chip()

    def _mark_unsaved(self):
        """Red 'Unsaved changes' chip. Single source of truth for the dirty
        state so every edit that affects the saved PNG — pixels AND layer ops —
        flips the status bar honestly."""
        self._dirty = True
        self._chip_state = "unsaved"
        self._supersede_flash()
        self._render_chip()

    def _mark_empty(self):
        """Grey 'Empty canvas' chip — the first-run / File > New empty state."""
        self._dirty = False
        self._chip_state = "empty"
        self._supersede_flash()
        self._render_chip()

    def _flash_save(self, text):
        """Surface a transient notice in the save chip, then restore the real
        save state after a moment so it never keeps showing a stale message."""
        if self._closed:
            return
        self._flash_token += 1
        token = self._flash_token
        try:
            self.save_lbl.set_markup(
                '<span foreground="#C8341E">●</span>  '
                '<span foreground="#6E695E">%s</span>'
                % GLib.markup_escape_text(text))
        except Exception:
            return
        # One armed restore at a time: back-to-back flashes replace it rather
        # than leaving the earlier one to fire into a window that may be gone.
        self._cancel_source("_chip_restore_src")
        self._chip_restore_src = GLib.timeout_add(2600, self._restore_chip,
                                                  token)

    def _restore_chip(self, token):
        self._chip_restore_src = 0
        if self._closed:
            return False
        # Only restore if no newer flash or state change happened meanwhile.
        if token == self._flash_token:
            self._render_chip()
        return False

    # ---------------- menus ----------------
    def menu_items(self, name):
        if name == "File":
            return [
                ("New    Ctrl+N", self._file_new),
                ("Open…    Ctrl+O", self._file_open),
                nbapp.SEP,
                ("Save    Ctrl+S", self._file_save),
                ("Save As…    Ctrl+Shift+S", self._file_save_as),
                nbapp.SEP,
            ] + super().menu_items(name)
        if name == "Edit":
            # The base Cut/Copy/Paste/Select All act on a focused text widget,
            # of which this app has none — they'd be dead. Undo/Redo come from
            # the SHARED builder every other editor uses, so all four word (and
            # key) them identically.
            return nbapp.undo_menu_items(self.history) + [
                nbapp.SEP,
                ("Copy Image", self._copy_image),
            ]
        if name == "View":
            # Dynamic labels so each action reads honestly for the current
            # state (menu_items is rebuilt every time the menu opens).
            vis = self.layers[self.active].visible
            return [
                ("Zoom In    Ctrl+Plus", lambda: self._step_zoom(1)),
                ("Zoom Out    Ctrl+Minus", lambda: self._step_zoom(-1)),
                ("Actual Size    Ctrl+0", lambda: self._set_zoom(1)),
                ("Fit in Window    Ctrl+9", self._zoom_fit),
                nbapp.SEP,
                # A tick on the SETTING, not "Hide Pixel Grid" on the action.
                # The grid only draws from 8x up, so below that the old label
                # offered to hide a grid that was not on screen — and after
                # pressing it, offered to show one that still would not be.
                (("✓ " if self.grid else "    ") + _t("Pixel Grid    G"),
                 self._toggle_grid),
                nbapp.SEP,
                ("Hide Active Layer" if vis else "Show Active Layer",
                 lambda: self._toggle_visible(None, self.active)),
                ("Show All Layers", self._show_all_layers),
            ]
        if name == "Image":
            # Shape fill is a TOOL setting and now lives in the dock beside
            # the shape tools, where it can be seen without opening anything.
            # The menu keeps the pair as two ticked choices rather than the
            # one item it was: that item named the state it would move TO
            # ("Fill Shapes" while shapes were outlines), so reading the menu
            # told you the opposite of what the next rectangle would do.
            # Translated before the mark is glued on — the menu builder looks
            # the WHOLE label up, and "✓ Fill Shapes" matches no catalog key.
            mark = ("    ", "✓ ") if self.fill_shapes else ("✓ ", "    ")
            return [
                ("Canvas Size…", self._canvas_size_prompt),
                nbapp.SEP,
                ("Flip Horizontal", lambda: self._flip(True)),
                ("Flip Vertical", lambda: self._flip(False)),
                nbapp.SEP,
                (mark[0] + _t("Outline Shapes"),
                 lambda: self._set_fill_shapes(False)),
                (mark[1] + _t("Fill Shapes"),
                 lambda: self._set_fill_shapes(True)),
            ]
        if name == "Layer":
            return [
                ("New Layer", self._add_layer),
                ("Delete Layer",
                 self._delete_layer if self.active != 0 else None),
                nbapp.SEP,
                ("Bring Forward",
                 (lambda: self._move_layer(1))
                 if self.active < len(self.layers) - 1 else None),
                ("Send Back",
                 (lambda: self._move_layer(-1)) if self.active > 0 else None),
                nbapp.SEP,
                ("Clear Layer", self._clear_active_layer),
                nbapp.SEP,
                ("Opacity 100%", lambda: self.op_scale.set_value(100)),
                ("Opacity  50%", lambda: self.op_scale.set_value(50)),
                ("Opacity  25%", lambda: self.op_scale.set_value(25)),
            ]
        return super().menu_items(name)

    # ---------------- keyboard ----------------
    def _on_key(self, w, ev):
        # Esc cancels an open prompt first (so it never re-triggers a close);
        # then Ctrl+N/O/S, Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y and the Ctrl zoom
        # keys; unmodified keys pick tools, step the brush size, drive the zoom
        # and toggle the grid; anything else falls through to the base.
        if ev.keyval == Gdk.KEY_Escape and self._close_saveprompt():
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            shift = ev.state & Gdk.ModifierType.SHIFT_MASK
            if ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self._redo() if shift else self._undo()
                return True
            if ev.keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._redo()
                return True
            if ev.keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._file_save_as() if shift else self._file_save()
                return True
            if ev.keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self._file_open()
                return True
            if ev.keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._file_new()
                return True
            if ev.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self._step_zoom(1)
                return True
            if ev.keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self._step_zoom(-1)
                return True
            if ev.keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._set_zoom(1)
                return True
            if ev.keyval in (Gdk.KEY_9, Gdk.KEY_KP_9):
                self._zoom_fit()
                return True
        elif (not (ev.state & Gdk.ModifierType.MOD1_MASK) and not self._drawing
                and self._saveprompt_layer is None and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            kl = Gdk.keyval_to_lower(ev.keyval)
            tool = _KEY_TOOLS.get(kl)
            if tool is not None:
                self._pick_tool(None, tool)
                return True
            if kl == Gdk.KEY_bracketleft:
                self._step_size(-1)
                return True
            if kl == Gdk.KEY_bracketright:
                self._step_size(1)
                return True
            if kl in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self._step_zoom(1)
                return True
            if kl in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self._step_zoom(-1)
                return True
            if kl in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._zoom_fit()
                return True
            if kl in (Gdk.KEY_1, Gdk.KEY_KP_1):
                self._set_zoom(1)
                return True
            if kl == Gdk.KEY_g:
                self._toggle_grid()
                return True
        return super()._on_key(w, ev)

    # ---------------- close guard ----------------
    def _cancel_source(self, attr):
        """Drop an owned GLib source. The id is cleared BEFORE the removal, so
        even a removal that raises (already dispatched, already removed) leaves
        nothing behind that a later cancel could hand to source_remove twice."""
        sid = getattr(self, attr, 0)
        setattr(self, attr, 0)
        if sid:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass

    def _on_destroy(self, *_):
        # Idempotent: "destroy" can reach this more than once (File ▸ Close on
        # an already-closing window, a second teardown pass at Shut Down). The
        # gate is raised FIRST so that a source GLib has ALREADY dispatched —
        # which source_remove can no longer stop — finds a dead window and
        # returns without touching a widget. This runs after delete-event has
        # had its say; the unsaved-work prompt vetoes the destroy, so reaching
        # here at all means the close is going through.
        if self._closed:
            return False
        self._closed = True
        self._cancel_source("_recentre_src")
        self._cancel_source("_fit_src")
        self._cancel_source("_chip_restore_src")
        return False

    def _on_delete(self, *_):
        # Both Esc and the red logo dot reach here via self.close(). When there
        # are unsaved changes, veto the destroy and show the save-prompt; the
        # prompt's buttons call self.destroy() directly, bypassing this guard.
        if not self._dirty:
            return False
        if self._saveprompt_layer is not None:
            return True   # a prompt is already up — don't stack another
        self._prompt_close()
        return True

    def _overlay_prompt(self, title, body, buttons, content=None):
        """Modal in-window prompt: a scrim over a warm-paper card with a serif
        title, a body line, and right-aligned buttons. `buttons` is a list of
        (label, style_class, callback) in display order; the callback runs after
        the prompt is dismissed, and a None callback (e.g. Cancel) just closes
        it. `content` is an optional widget shown under the body line. Only one
        prompt shows at a time; Esc or a scrim click dismisses it."""
        # Who gets the keyboard back when this card goes. Captured before
        # the button below takes focus, and before the old card is torn
        # down, so a second prompt does not record the first one's button.
        if self._saveprompt_layer is None:
            self._prompt_return_focus = self.get_focus()
        self._close_saveprompt()
        self._close_menu()
        self._close_about()
        alloc = self.get_allocation()
        # Size the scrim to the LIVE window, falling back to the real primary
        # monitor size — never a hardcoded 1920x1080.
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh

        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.get_style_context().add_class("ilscrim")
        scrim.connect("button-press-event",
                      lambda *a: (self._close_saveprompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("ilprompt")
        tl = Gtk.Label(label=title, xalign=0)
        tl.get_style_context().add_class("ilprompttitle")
        card.pack_start(tl, False, False, 0)
        bd = Gtk.Label(label=body, xalign=0)
        bd.get_style_context().add_class("ilpromptbody")
        bd.set_line_wrap(True)
        bd.set_max_width_chars(34)
        card.pack_start(bd, False, False, 0)
        if content is not None:
            card.pack_start(content, False, False, 0)

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        focus_btn = None
        for label, style, cb in buttons:
            btn = Gtk.Button(label=label)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class(style)
            btn.connect(
                "clicked",
                lambda _w, fn=cb: (self._close_saveprompt(), fn and fn())[1])
            btnrow.pack_start(btn, False, False, 0)
            # Rest keyboard focus on the safe (Cancel) button so a stray
            # Space/Enter can never fire a destructive action by default.
            if focus_btn is None or style == "ilpromptcancel":
                focus_btn = btn
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # centre on the real window using the card's measured size
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 420
        ch = nat.height if nat.height > 1 else 200
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        self._saveprompt_layer = layer
        if focus_btn is not None:
            focus_btn.grab_focus()

    def _confirm_discard(self, consequence, on_confirm):
        """Run `on_confirm` immediately when there is nothing to lose; otherwise
        ask first (Cancel / Discard) so New / Open never silently drop unsaved
        work. `consequence` completes the sentence shown to the user."""
        if not self._dirty:
            on_confirm()
            return
        # ONE heading for one situation: the close guard below says the same
        # thing, and this app used to name it two different ways.
        self._overlay_prompt(
            _t("Unsaved changes"),
            _t("The current image has unsaved changes. ") + _t(consequence),
            [("Cancel", "ilpromptcancel", None),
             ("Discard", "ilpromptdiscard", on_confirm)])

    def _prompt_close(self):
        # Unsaved-work guard on close (Esc / logo). Discard drops the work,
        # Cancel keeps the window open, Save writes the PNG first then closes.
        self._overlay_prompt(
            _t("Unsaved changes"),
            _t("This image has unsaved changes. Save it before closing?"),
            [("Discard", "ilpromptdiscard",
              lambda: self._close_and_destroy(False)),
             ("Cancel", "ilpromptcancel", None),
             ("Save", "ilpromptok",
              lambda: self._close_and_destroy(True))])

    def _close_saveprompt(self):
        layer = self._saveprompt_layer
        if layer is not None:
            return_focus = getattr(self, "_prompt_return_focus", None)
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._saveprompt_layer = None
            self._prompt_return_focus = None
            # The card deliberately parks focus on its safe button, and removing
            # the layer takes that button with it — leaving GTK no focus owner
            # at all, so typing and keyboard navigation go nowhere until the
            # person clicks back into the drawing. Restore after the removal,
            # and tolerate an invoker replaced while the card was open.
            if return_focus is not None:
                try:
                    return_focus.grab_focus()
                except Exception:
                    pass
            return True
        return False

    def _close_and_destroy(self, save):
        self._close_saveprompt()
        if save and not self._file_save():
            # Save As was cancelled or the write failed — abort the close so
            # the user doesn't lose unsaved work to a dismissed chooser
            return
        self.destroy()           # destroy skips delete-event, so no re-prompt

    # ---------------- css ----------------
    def _install_css(self):
        css = b"""
        .dock *, .lpanel *, .statusbar * {
            font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .caption { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                   font-weight: 700; }

        .stepbtn { min-width: 30px; min-height: 30px; padding: 0;
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; box-shadow: none; }
        .stepbtn:hover { background: #F4F2EC; }
        .stepbtn.sel { background: #EAE3D2; border-color: #B3AD9E; }
        /* a mark that carries a word (Outline / Filled) needs room for it */
        .stepbtn.wide { padding: 0 9px; }
        .marklabel { font-size: 12px; color: #1A1916; }
        /* the pixel readout: a number in a field, not a button */
        .numfield { font-size: 13px; color: #1A1916; background: #FCFBF8;
                    border: 1px solid #C9C4B6; border-radius: 8px; }
        .chip { border: none; }

        /* A settings group the current tool cannot use. It keeps its place;
           nothing in the dock moves when the tool changes. It goes quiet, so
           a dimmed group is itself the answer to "why is the size ignored?".
           The caption is matched directly: the theme sets a colour on label
           nodes, so a colour on the container never reaches its text. */
        .dim .caption, .dim label, .dim .marklabel { color: #B3AD9E; }
        .dim .numfield { color: #B3AD9E; background: #F4F2EC;
                         border-color: #D7D2C5; }
        .dim .stepbtn { background: #F4F2EC; border-color: #D7D2C5; }
        .dim .stepbtn.sel { background: #EFEBE0; border-color: #D7D2C5; }

        /* ---- left dock: tools, tool settings, colour ---- */
        /* The dock sits in a scroller, so the papertone field and the rule
           against the canvas go on the SCROLLER and its viewport as well.
           Otherwise the strip beside a scrollbar comes up in the theme's
           default background, exactly as the canvas mat has to do. */
        .dockscroll, .dockscroll viewport { background: #F1EEE6; }
        .dockscroll { border-right: 1px solid #C9C4B6; }
        .dock { background: #F1EEE6; padding: 10px 12px; }
        .hsep { background: #D7D2C5; min-height: 1px; }
        /* Tool buttons carry a NAME, so they are rows rather than 26px
           squares: three of the eight icons are the same diagonal implement at
           that size and the word is what tells them apart. */
        .toolbtn { min-height: 30px; padding: 0 8px;
                   background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; box-shadow: none; }
        .toolbtn:hover { background: #F4F2EC; }
        .toolname { font-size: 12px; color: #1A1916; }
        .toolbtn.sel { background: #C8341E; border-color: #C8341E; }
        .toolbtn.sel:hover { background: #B12C18; border-color: #B12C18; }
        .toolbtn.sel .toolname { color: #FCFBF8; font-weight: 600; }
        .swatch { padding: 0; margin: 0; min-width: 22px; min-height: 22px;
                  background: transparent; border: none; box-shadow: none; }
        .colorname { font-size: 12px; color: #1A1916; }
        .custombtn { min-height: 30px; padding: 2px 10px; font-size: 12px;
                     color: #1A1916; background: #FCFBF8;
                     border: 1px solid #C9C4B6; border-radius: 8px;
                     box-shadow: none; }
        .custombtn:hover { background: #F4F2EC; }
        .savestate { font-size: 12px; color: #6E695E; }

        /* ---- canvas mat ---- */
        /* Papertone field on the scroll AND its viewport, so the field fills the
           area whether the canvas is centred (large panel) or scrolled. */
        .mat, .mat viewport, .canvasfield { background: #DED4C2; }
        .canvasframe { background: #FCFBF8; padding: 1px;
                       border: 1px solid #C9C4B6;
                       box-shadow: 4px 4px 0 rgba(26,25,22,0.10); }

        /* ---- layers panel ---- */
        .lpanel { background: #F1EEE6; border-left: 1px solid #C9C4B6; }
        .lhead { padding: 16px 16px; border-bottom: 1px solid #D7D2C5; }
        .ltitle { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                  font-weight: 700; }
        .liconbtn { min-width: 26px; min-height: 26px; padding: 0; margin-left: 5px;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; box-shadow: none; }
        .liconbtn:hover { background: #F4F2EC; }
        .liconbtn.disabled { background: #F1EEE6; border-color: #D7D2C5; }
        .llist { padding: 8px 10px; }
        .lrow { padding: 8px 10px; border-radius: 6px; box-shadow: none;
                background: transparent; border: none; }
        .lrow:hover { background: #F4F2EC; }
        .lrow.active { background: #FCFBF8; box-shadow: inset 3px 0 0 #C8341E; }
        /* one line under a one-row list, so the panel reads as a document with
           one layer rather than as a panel that failed to fill */
        .lempty { font-size: 12px; color: #9A9484; padding: 10px 10px 0 10px; }
        .lname { font-size: 14px; color: #1A1916; }
        .lname.hidden { color: #9A9484; }
        .lrow.active .lname { font-weight: 600; }
        .lopacity { font-size: 11px; color: #9A9484; }
        .eyebtn { min-width: 26px; min-height: 26px; padding: 0;
                  background: transparent; border: none; box-shadow: none; }
        .lfoot { padding: 16px 16px; border-top: 1px solid #D7D2C5; }
        .opacity { padding: 0; }
        .opacity trough { min-height: 4px; background: #D7D2C5;
                          border: none; border-radius: 100px; }
        .opacity highlight { background: #1A1916; border-radius: 100px; }
        .opacity slider { min-width: 16px; min-height: 16px; margin: -7px;
                          background: #1A1916; border: none; border-radius: 50%; }

        /* ---- status bar ---- */
        /* .statusbar itself is Papertone's (see the theme): one strip,
           one look, every app. Only this app's own label class stays. */
        .stlabel { font-size: 12px; color: #6E695E; }

        /* ---- prompts (unsaved changes, mix a colour, canvas size) ---- */
        .ilscrim { background: rgba(26,25,22,0.28); }
        /* min-width sets the card's measure: a wrapping label's natural width
           is computed from the font's AVERAGE character, which for this face
           is far narrower than the real text, so the body was breaking into a
           ragged four-line column half the width of its own title. */
        .ilprompt { background: #FCFBF8; border: 1px solid #1A1916;
                    padding: 26px 30px; min-width: 330px; }
        .ilprompt * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .ilprompttitle { font-family: "Newsreader","Liberation Serif",serif;
                         font-size: 20px; color: #1A1916; }
        .ilpromptbody { font-size: 14px; color: #6E695E; }
        .mixname { font-size: 13px; color: #1A1916; }
        .presetbtn { min-height: 28px; padding: 0 8px; font-size: 12px;
                     color: #1A1916; background: #FCFBF8;
                     border: 1px solid #C9C4B6; border-radius: 8px;
                     box-shadow: none; }
        .presetbtn:hover { background: #F4F2EC; }
        .presetbtn.sel { background: #EAE3D2; border-color: #B3AD9E;
                         font-weight: 600; }
        .sizeentry { min-height: 28px; font-size: 13px; }
        .ilpromptok { min-height: 34px; padding: 0 18px; border: 1px solid #1A1916;
                      border-radius: 8px; background: #1A1916; color: #FCFBF8;
                      box-shadow: none; font-size: 14px; font-weight: 600; }
        .ilpromptok:hover { background: #2A2620; }
        .ilpromptcancel { min-height: 34px; padding: 0 16px; color: #2A2620;
                          border: 1px solid #C9C4B6; border-radius: 8px;
                          background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .ilpromptcancel:hover { background: #F4F2EC; }
        .ilpromptdiscard { min-height: 34px; padding: 0 16px; color: #C8341E;
                           border: 1px solid #E0B3AA; border-radius: 8px;
                           background: #FCFBF8; box-shadow: none; font-size: 14px; }
        .ilpromptdiscard:hover { background: #F6E7E3; }

        /* The system theme sets `* { color: ink }`, which matches a button's
           LABEL node directly, so a colour set on the button itself never
           reaches its text: the ink-filled primary button came up as a blank
           black slab with its "Save" invisible, and Discard lost its red. */
        .ilpromptok label { color: #FCFBF8; }
        .ilpromptdiscard label { color: #C8341E; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Illustrator)
