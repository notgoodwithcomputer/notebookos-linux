#!/usr/bin/env python3
"""Comics - the Notebook OS pixel comic zine studio.

Comics joins Illustrator's byte-exact, non-antialiased drawing discipline to
Novel's half-letter booklet workflow.  A single 1650 x 2550 pixel page model is
the source for the canvas, thumbnails, shared PDF and imposed zine: lettering
and objects are flattened once and are never laid out again for print.  Komika
Hand is preferred for lettering; Pango falls back per character for scripts
outside its Western-European coverage while retaining hard pixel edges.
"""
import base64
import collections
import copy
import io
import json
import math
import os
import sys
import tempfile
import time

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango, PangoCairo  # noqa: E402

import nbapp
import nbicons
import nbpicker
import nbprint
import nbjobs
from nbi18n import _t  # noqa: E402

PAGE_PX_W = 1650
PAGE_PX_H = 2550
PRINT_SCALE = 0.24
LEGACY_PAGE_W = 550
LEGACY_PAGE_H = 850
STORE_FORMAT = 2
REBASE = 3
PAGE_MIN = 4
PAGE_MAX = 32
PAGE_NEW = 8
LAYER_MAX = 4
BUBBLE_MIN_W = 72
BUBBLE_MIN_H = 72
UNDO_DEPTH = 200
HISTORY_BYTES = 96 * 1024 * 1024
DOCK_W = 240
PANEL_W = 240
SIZE_MIN, SIZE_MAX = 1, 192
SIZE_RAMP = (3, 6, 12, 24, 48)
BUBBLE_SIZES = (30, 40, 50, 60, 80)
ZOOM_MIN, ZOOM_MAX = 1 / 8, 32
ZOOM_STEPS = (1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2,
              1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32)
GRID_FROM = 8

NB_HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
DOCS_DIR = os.path.join(NB_HOME, "Documents")
PICS_DIR = os.path.join(NB_HOME, "Pictures")
COMICS_FILE = os.path.join(NB_HOME, ".config", "notebook", "comics.json")
PREFS_FILE = os.path.join(NB_HOME, ".config", "notebook", "comics-prefs.json")

TOOLS = (("select", "Select", "V"), ("pencil", "Pencil", "P"),
         ("brush", "Brush", "B"), ("eraser", "Eraser", "E"),
         ("fill", "Fill", "F"), ("line", "Line", "L"),
         ("rect", "Rectangle", "R"), ("ellipse", "Ellipse", "O"),
         ("picker", "Eyedropper", "I"), ("bubble", "Bubble", "W"),
         ("panel", "Panel", "N"))
TOOL_HINTS = {
    "select": "Click a bubble or panel. Drag to move it; handles resize; Delete removes it.",
    "pencil": "Drag to draw. Square tip, hard edges.",
    "brush": "Drag to draw. Round tip, hard edges.",
    "eraser": "Drag to rub back to the paper.",
    "fill": "Click an area to flood it with the colour.",
    "line": "Drag end to end. Hold Shift for 45\u00b0 steps.",
    "rect": "Drag corner to corner. Hold Shift for a square.",
    "ellipse": "Drag corner to corner. Hold Shift for a circle.",
    "picker": "Click the artwork to take that colour.",
    "bubble": "Click to place a word bubble. Click a bubble to edit its text.",
    "panel": "Drag corner to corner to frame a panel.",
}

_HUES = (("Red", 0), ("Coral", 14), ("Orange", 30), ("Amber", 44),
         ("Yellow", 56), ("Lime", 82), ("Green", 122), ("Emerald", 150),
         ("Teal", 172), ("Cyan", 188), ("Azure", 205), ("Blue", 222),
         ("Indigo", 244), ("Purple", 276), ("Magenta", 305), ("Pink", 332))
_VALUES = (("Darkest %s", .70, .28, 250, 10), ("Dark %s", .85, .48, 250, 5),
           (None, .90, .72, None, 0), ("Bright %s", .85, .95, 50, 5),
           ("Pale %s", .35, 1., 50, 4))
_MUTED = ("Muted %s", .30, .62)
_NEUTRALS = (("Black",0),("Ink",0x24),("Slate",0x48),("Grey",0x70),("Silver",0x99),("Ash",0xBB),("Paper",0xDD),("White",0xFF))
_STAPLES = (("Brown","#6B4A2F"),("Tan","#C9A26A"),("Cream","#F2E4C4"),("Olive","#6E7B3F"),("Navy","#20325C"),("Maroon","#5E1F28"),("Gold","#D9A21B"),("Peach","#F2B79A"))
PAL_COLS = 16


def _hsv_hex(h,s,v):
    h%=360.; c=v*s; x=c*(1-abs((h/60.)%2-1)); m=v-c
    r,g,b=((c,x,0),(x,c,0),(0,c,x),(0,x,c),(x,0,c),(c,0,x))[int(h//60)%6]
    return "#%02X%02X%02X"%(round((r+m)*255),round((g+m)*255),round((b+m)*255))


def _toward(h,target,amount):
    d=((target-h+180)%360)-180
    return float(target) if abs(d)<=amount else (h+(amount if d>0 else -amount))%360


def _build_palette():
    colours=[]; parts=[]
    for template,s,v,target,amount in _VALUES:
        for name,hue in _HUES:
            colours.append(_hsv_hex(_toward(hue,target,amount) if target is not None else hue,s,v)); parts.append((template,name))
    template,s,v=_MUTED
    for name,hue in _HUES: colours.append(_hsv_hex(hue,s,v)); parts.append((template,name))
    for name,g in _NEUTRALS: colours.append("#%02X%02X%02X"%(g,g,g)); parts.append((None,name))
    for name,colour in _STAPLES: colours.append(colour); parts.append((None,name))
    return colours,parts


PALETTE, PALETTE_PARTS = _build_palette()


def palette_name(index):
    template,word=PALETTE_PARTS[index]
    return (_t(template)%_t(word)) if template else _t(word)


def mix_name(colour):
    r,g,b=(int(colour[i:i+2],16) for i in (1,3,5)); best=0; distance=None
    for i,item in enumerate(PALETTE):
        rr,gg,bb=(int(item[j:j+2],16) for j in (1,3,5)); d=(r-rr)**2+(g-gg)**2+(b-bb)**2
        if distance is None or d<distance: best,distance=i,d
    return palette_name(best)

CSS = b"""
.comics { background: #FCFBF8; color: #1A1916; }
.comics * { border-radius: 0; }
.comics-dock, .comics-side { background: #FCFBF8; }
.comics-mat { background: #DED4C2; }
.comics-group { color: #6E695E; }
.comics-row { border-bottom: 1px solid #C9C4B6; }
.comics-row:checked { background: #EAE3D2; }
.comics button.selected { background: #EAE3D2; }
.comics-prompt { background: #FCFBF8; border: 1px solid #C9C4B6; padding: 24px; }
.comics-scrim { background: rgba(26,25,22,0.28); }
.comics .opacity trough { min-height: 4px; background: #D7D2C5;
                          border: none; border-radius: 100px; }
.comics .opacity highlight { background: #1A1916; border-radius: 100px; }
.comics .opacity slider { min-width: 16px; min-height: 16px; margin: -7px;
                          background: #1A1916; border: none;
                          border-radius: 50%; }
.comics-selection { color: #C8341E; }
.comics-saved { color: #7FA98C; }
.comics-unsaved { color: #C8341E; }
"""


def _rgb(hex_):
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def view_pixel(x, y, zoom, width=PAGE_PX_W, height=PAGE_PX_H, clamp=False):
    px = int(math.floor(float(x) / float(zoom)))
    py = int(math.floor(float(y) / float(zoom)))
    if clamp:
        px = max(0, min(width - 1, px))
        py = max(0, min(height - 1, py))
    return px, py


def fit_zoom(width, height, available_width, available_height):
    limit = min(float(available_width) / width,
                float(available_height) / height)
    choices = [z for z in ZOOM_STEPS if z <= limit]
    return choices[-1] if choices else ZOOM_MIN


def px4(hex_):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return bytes((b, g, r, 255))


CLEAR4 = b"\x00\x00\x00\x00"
_RUNS = {}


# Illustrator.py is the source of truth for this shared integer geometry.
def brush_runs(size, shape):
    key = (int(size), shape)
    if key in _RUNS:
        return _RUNS[key]
    n = max(SIZE_MIN, min(SIZE_MAX, int(size)))
    o = n // 2
    out = []
    if shape == "round" and n > 2:
        c = (n - 1) / 2.0
        rr = (n / 2.0 - 0.15) ** 2
        for j in range(n):
            row = [i for i in range(n)
                   if (i - c) ** 2 + (j - c) ** 2 <= rr]
            if row:
                out.append((j - o, row[0] - o, row[-1] - o))
    else:
        out = [(j - o, -o, n - 1 - o) for j in range(n)]
    _RUNS[key] = tuple(out)
    return _RUNS[key]


def brush_pixels(size, shape):
    return sum(b - a + 1 for _y, a, b in brush_runs(size, shape))


def _line_points(x0, y0, x1, y1):
    x0, y0, x1, y1 = map(int, (x0, y0, x1, y1))
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x1 > x0 else -1), (1 if y1 > y0 else -1)
    err, out = dx - dy, []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return out
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def _ellipse_spans(x0, y0, x1, y1):
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = (x1 - x0 + 1) / 2.0, (y1 - y0 + 1) / 2.0
    out = []
    for y in range(y0, y1 + 1):
        v = 1.0 - ((y - cy) / ry) ** 2
        if v < 0:
            continue
        dx = math.sqrt(v) * rx
        xa = int(math.ceil(cx - dx - 0.5))
        xb = int(math.floor(cx + dx + 0.5)) - 1
        if xb < xa:
            xa = xb = int(round(cx))
        out.append((y, max(x0, xa), min(x1, xb)))
    return out


def _ellipse_outline(spans):
    out, prev = [], None
    for i, (y, xa, xb) in enumerate(spans):
        if prev is None or i == len(spans) - 1:
            out.extend((x, y) for x in range(xa, xb + 1))
        else:
            pa, pb = prev
            out.extend((x, y) for x in range(min(xa, pa), max(xa, pa) + 1))
            out.extend((x, y) for x in range(min(xb, pb), max(xb, pb) + 1))
        prev = xa, xb
    return out


def _snap45(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if not dx and not dy:
        return b
    ax, ay = abs(dx), abs(dy)
    if ax > 2 * ay:
        return b[0], a[1]
    if ay > 2 * ax:
        return a[0], b[1]
    n = max(ax, ay)
    return a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n)


def _square(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = max(abs(dx), abs(dy))
    return a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n)


def _surface(fill_white=False):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, PAGE_PX_W, PAGE_PX_H)
    if fill_white:
        cr = cairo.Context(s)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
    return s


def _png(surface):
    out = io.BytesIO()
    surface.write_to_png(out)
    return out.getvalue()


def _decode(raw):
    return cairo.ImageSurface.create_from_png(io.BytesIO(raw))


def _write_pixel(surface, x, y, colour):
    if not (0 <= x < PAGE_PX_W and 0 <= y < PAGE_PX_H):
        return
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    data[y * stride + x * 4:y * stride + x * 4 + 4] = px4(colour)
    surface.mark_dirty()


def _stamp(surface, x, y, size, shape, colour):
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    pix = px4(colour)
    for dy, xa, xb in brush_runs(size, shape):
        yy, left, right = y + dy, x + xa, x + xb
        if yy < 0 or yy >= PAGE_PX_H or right < 0 or left >= PAGE_PX_W:
            continue
        left, right = max(0, left), min(PAGE_PX_W - 1, right)
        data[yy * stride + left * 4:yy * stride + (right + 1) * 4] = pix * (right - left + 1)
    surface.mark_dirty()


def _spans(surface, spans, colour):
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    pix = px4(colour)
    for y, xa, xb in spans:
        if 0 <= y < PAGE_PX_H:
            xa, xb = max(0, xa), min(PAGE_PX_W - 1, xb)
            if xb >= xa:
                data[y * stride + xa * 4:y * stride + (xb + 1) * 4] = pix * (xb - xa + 1)
    surface.mark_dirty()


def _polygon_spans(points):
    if not points:
        return []
    out = []
    for y in range(max(0, min(p[1] for p in points)),
                   min(PAGE_PX_H - 1, max(p[1] for p in points)) + 1):
        hits = []
        for a, b in zip(points, points[1:] + points[:1]):
            if a[1] == b[1] or y < min(a[1], b[1]) or y >= max(a[1], b[1]):
                continue
            hits.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
        hits.sort()
        for i in range(0, len(hits) - 1, 2):
            out.append((y, int(math.ceil(hits[i])), int(math.floor(hits[i + 1]))))
    return out


def _ring_rect(surface, x, y, w, h, border=6, colour="#000000"):
    b = max(1, int(border))
    _spans(surface, [(yy, x, x + w - 1) for yy in range(y, y + b)] +
           [(yy, x, x + w - 1) for yy in range(y + h - b, y + h)] +
           [(yy, x, x + b - 1) for yy in range(y + b, y + h - b)] +
           [(yy, x + w - b, x + w - 1) for yy in range(y + b, y + h - b)], colour)


def _bubble_defaults(x=555, y=1140):
    return {"style": "speech", "x": int(x), "y": int(y), "w": 540,
            "h": 270, "tail": [int(x - 135), int(y + 405)], "text": "",
            "size": 40, "align": "c", "bold": False, "italic": False}


def _text_layout(cr, bubble):
    style = bubble.get("style", "speech")
    pad = 24
    if style in ("speech", "thought"):
        tw, th = bubble["w"] * 0.72, bubble["h"] * 0.72
        tx = bubble["x"] + (bubble["w"] - tw) / 2
        ty = bubble["y"] + (bubble["h"] - th) / 2
    else:
        tx, ty = bubble["x"] + pad, bubble["y"] + pad
        tw, th = bubble["w"] - pad * 2, bubble["h"] - pad * 2
    layout = PangoCairo.create_layout(cr)
    layout.set_width(int(max(1, tw) * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_alignment(Pango.Alignment.LEFT if bubble.get("align") == "l"
                         else Pango.Alignment.CENTER)
    fd = Pango.FontDescription()
    fd.set_family("Komika Hand")
    fd.set_absolute_size(int(bubble.get("size", 40) * Pango.SCALE))
    if bubble.get("bold"):
        fd.set_weight(Pango.Weight.BOLD)
    if bubble.get("italic"):
        fd.set_style(Pango.Style.ITALIC)
    layout.set_font_description(fd)
    layout.set_text(str(bubble.get("text", "")), -1)
    return layout, tx, ty, tw, th


def bubble_required_height(bubble):
    probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    layout, _x, _y, _w, _h = _text_layout(cairo.Context(probe), bubble)
    need = layout.get_pixel_size()[1] + 48
    if bubble.get("style") in ("speech", "thought"):
        need = int(math.ceil(need / 0.72))
    return max(int(bubble.get("h", BUBBLE_MIN_H)), need)


def grow_bubble(bubble):
    bubble["h"] = bubble_required_height(bubble)
    return bubble


def _starburst(b):
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    out = []
    for i in range(28):
        a = -math.pi / 2 + i * math.pi / 14
        k = 1.0 if i % 2 == 0 else 0.72
        out.append((int(round(cx + math.cos(a) * b["w"] / 2 * k)),
                    int(round(cy + math.sin(a) * b["h"] / 2 * k))))
    return out


def raster_bubble(bubble, surface=None):
    """Rasterise one complete object with no fractional artwork coverage."""
    s = surface or _surface(False)
    b = grow_bubble(copy.deepcopy(bubble))
    x, y, w, h = b["x"], b["y"], b["w"], b["h"]
    style, tail = b.get("style", "speech"), b.get("tail")
    if style in ("speech", "thought"):
        _spans(s, _ellipse_spans(x, y, x + w - 1, y + h - 1), "#000000")
        _spans(s, _ellipse_spans(x + 6, y + 6, x + w - 7, y + h - 7), "#FFFFFF")
    elif style == "shout":
        pts = _starburst(b)
        _spans(s, _polygon_spans(pts), "#FFFFFF")
        for a, c in zip(pts, pts[1:] + pts[:1]):
            for px, py in _line_points(a[0], a[1], c[0], c[1]):
                _stamp(s, px, py, 6, "round", "#000000")
    else:
        _spans(s, [(yy, x, x + w - 1) for yy in range(y, y + h)], "#FFFFFF")
        _ring_rect(s, x, y, w, h, 6)
    if tail and style in ("speech", "shout"):
        # The tail roots ON the rim, aimed at the tip: base points sit on the
        # bubble's boundary either side of the tip's bearing, the white
        # triangle opens the rim between them, and edge ink is laid ONLY
        # outside the bubble's inner white — so the tail joins the rim as one
        # line and never knifes across the lettering area.
        cx, cy = x + (w - 1) / 2.0, y + (h - 1) / 2.0
        tx, ty = map(int, tail)
        theta = math.atan2(ty - cy, tx - cx)
        spread = 0.55 if style == "shout" else 0.42
        rx, ry = w / 2.0, h / 2.0
        p1 = (int(round(cx + math.cos(theta - spread) * rx)),
              int(round(cy + math.sin(theta - spread) * ry)))
        p2 = (int(round(cx + math.cos(theta + spread) * rx)),
              int(round(cy + math.sin(theta + spread) * ry)))
        _spans(s, _polygon_spans([p1, p2, (tx, ty)]), "#FFFFFF")
        irx, iry = max(1.0, rx - 6.0), max(1.0, ry - 6.0)
        for end in (p1, p2):
            for lx, ly2 in _line_points(end[0], end[1], tx, ty):
                if ((lx - cx) / irx) ** 2 + ((ly2 - cy) / iry) ** 2 >= 1.0:
                    _stamp(s, lx, ly2, 6, "round", "#000000")
    elif tail and style == "thought":
        tx, ty = map(int, tail)
        sx, sy = x + w // 3, y + h
        for k, (ew, eh) in enumerate(((30, 21), (18, 12)), 1):
            cx = int(sx + (tx - sx) * k / 3)
            cy = int(sy + (ty - sy) * k / 3)
            _spans(s, _ellipse_spans(cx - ew // 2, cy - eh // 2,
                                     cx + ew // 2, cy + eh // 2), "#000000")
            _spans(s, _ellipse_spans(cx - ew // 2 + 6, cy - eh // 2 + 6,
                                     cx + ew // 2 - 6, cy + eh // 2 - 6), "#FFFFFF")
    cr = cairo.Context(s)
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    opts = cairo.FontOptions()
    opts.set_antialias(cairo.ANTIALIAS_NONE)
    opts.set_hint_style(cairo.HINT_STYLE_FULL)
    cr.set_font_options(opts)
    layout, tx, ty, _tw, _th = _text_layout(cr, b)
    cr.set_source_rgb(0, 0, 0)
    cr.move_to(int(tx), int(ty))
    PangoCairo.update_layout(cr, layout)
    PangoCairo.show_layout(cr, layout)
    s.flush()
    return s


class Layer:
    def __init__(self, name="Layer 1", visible=True, opacity=100,
                 surface=None, png=None, extra=None, dirty=None):
        self.name = name
        self.visible = bool(visible)
        self.opacity = max(0, min(100, int(opacity)))
        self.surface = surface
        self.png = png
        self._extra = dict(extra or {})
        self.dirty = (surface is not None and png is None) if dirty is None else bool(dirty)
        self.revision = 0

    def decode(self):
        if self.surface is None:
            self.surface = _decode(self.png) if self.png else _surface(False)
        return self.surface

    def encode(self):
        if not self.dirty and self.png is not None:
            return self.png
        if self.surface is not None:
            self.png = _png(self.surface)
        elif self.png is None:
            self.png = _png(_surface(False))
        self.dirty = False
        return self.png

    def touch(self):
        self.dirty = True
        self.revision += 1

    def serial(self):
        out = dict(self._extra)
        out.update({"name": self.name, "visible": self.visible,
                    "opacity": self.opacity,
                    "png": base64.b64encode(self.encode()).decode("ascii")})
        return out


def new_page():
    return {"layers": [Layer(surface=_surface(True))], "panels": [],
            "bubbles": [], "mask_gutters": False, "_extra": {}}


def _page_serial(page):
    out = dict(page.get("_extra", {}))
    out.update({"layers": [ly.serial() for ly in page["layers"]],
                "panels": [dict(x.get("_extra", {}), **{k: x[k] for k in
                           ("x", "y", "w", "h", "border")}) for x in page["panels"]],
                "bubbles": [dict(x.get("_extra", {}), **{k: x.get(k) for k in
                            ("style", "x", "y", "w", "h", "tail", "text",
                             "size", "align", "bold", "italic")}) for x in page["bubbles"]],
                "mask_gutters": bool(page.get("mask_gutters", False))})
    return out


def _upscale_legacy(surface):
    out = _surface(False)
    cr = cairo.Context(out)
    cr.scale(REBASE, REBASE)
    pattern = cairo.SurfacePattern(surface)
    pattern.set_filter(cairo.FILTER_NEAREST)
    cr.set_source(pattern)
    cr.paint()
    out.flush()
    return out


def _parse_page(raw, errors, legacy=False):
    if not isinstance(raw, dict) or not isinstance(raw.get("layers"), list):
        return None
    layers = []
    for item in raw["layers"][:LAYER_MAX]:
        if not isinstance(item, dict):
            continue
        known = {"name", "visible", "opacity", "png"}
        try:
            data = base64.b64decode(item.get("png", ""), validate=True)
            surface = _decode(data)
            expected = ((LEGACY_PAGE_W, LEGACY_PAGE_H) if legacy
                        else (PAGE_PX_W, PAGE_PX_H))
            if (surface.get_width(), surface.get_height()) != expected:
                raise ValueError("page layer size")
            if legacy:
                surface = _upscale_legacy(surface)
        except Exception:
            errors.append("A page layer could not be read.")
            data, surface = _png(_surface(False)), None
        layers.append(Layer(str(item.get("name", "Layer %d" % (len(layers) + 1))),
                            item.get("visible", True), item.get("opacity", 100),
                            surface=surface, png=(None if legacy else data),
                            extra={k: v for k, v in item.items() if k not in known},
                            dirty=legacy))
    if not layers:
        layers = [Layer(surface=_surface(True))]
    panels = []
    for item in raw.get("panels", []):
        if not isinstance(item, dict):
            continue
        try:
            p = {k: int(item.get(k, d)) for k, d in
                 (("x", 0), ("y", 0), ("w", 72), ("h", 72), ("border", 9))}
        except (TypeError, ValueError):
            continue
        if legacy:
            for key in ("x", "y", "w", "h", "border"):
                p[key] *= REBASE
        p["border"] = max(1, min(24, p["border"]))
        p["_extra"] = {k: v for k, v in item.items() if k not in p}
        panels.append(p)
    bubbles = []
    for item in raw.get("bubbles", []):
        if not isinstance(item, dict):
            continue
        b = _bubble_defaults(item.get("x", 0), item.get("y", 0))
        for k in b:
            if k in item:
                b[k] = item[k]
        if legacy:
            for key in ("x", "y", "w", "h", "size"):
                b[key] = int(b[key]) * REBASE
            if b.get("tail") is not None:
                b["tail"] = [int(b["tail"][0]) * REBASE,
                             int(b["tail"][1]) * REBASE]
        b["w"] = max(BUBBLE_MIN_W, int(b["w"]))
        b["h"] = max(BUBBLE_MIN_H, int(b["h"]))
        b["_extra"] = {k: v for k, v in item.items() if k not in b}
        bubbles.append(b)
    known = {"layers", "panels", "bubbles", "mask_gutters"}
    return {"layers": layers, "panels": panels, "bubbles": bubbles,
            "mask_gutters": bool(raw.get("mask_gutters", False)),
            "_extra": {k: v for k, v in raw.items() if k not in known}}


class ComicDocument:
    def __init__(self, pages=None, extra=None, doc_path=None):
        self.pages = pages or [new_page() for _ in range(PAGE_NEW)]
        self.active = 0
        self._extra = dict(extra or {})
        self.doc_path = doc_path

    def serial(self):
        out = dict(self._extra)
        out.update({"format": STORE_FORMAT, "app": "comics",
                    "pages": [_page_serial(p) for p in self.pages]})
        return out

    def bytes(self):
        return json.dumps(self.serial(), sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or data.get("format") not in (1, STORE_FORMAT) or data.get("app") != "comics":
            return None, []
        legacy = data.get("format") == 1
        raw = data.get("pages")
        if not isinstance(raw, list) or not PAGE_MIN <= len(raw) <= PAGE_MAX:
            return None, []
        errors, pages = [], []
        for item in raw:
            page = _parse_page(item, errors, legacy=legacy)
            if page is None:
                return None, errors
            pages.append(page)
        known = {"format", "app", "pages"}
        return cls(pages, {k: v for k, v in data.items() if k not in known}), errors

    def add_page(self, index=None, duplicate=False):
        if len(self.pages) >= PAGE_MAX:
            return False
        at = self.active + 1 if index is None else max(0, min(len(self.pages), index))
        page = copy.deepcopy(self.pages[self.active]) if duplicate else new_page()
        self.pages.insert(at, page)
        self.active = at
        return True

    def delete_page(self):
        if len(self.pages) <= PAGE_MIN:
            return False
        self.pages.pop(self.active)
        self.active = min(self.active, len(self.pages) - 1)
        return True

    def move_page(self, delta):
        to = self.active + delta
        if not 0 <= to < len(self.pages):
            return False
        self.pages[self.active], self.pages[to] = self.pages[to], self.pages[self.active]
        self.active = to
        return True


def panel_layout(preset, margin=90, gutter=42):
    rows, cols = ((1, 1), (2, 1), (3, 1), (2, 2), (3, 2), (3, 3))[int(preset)]
    margin, gutter = max(0, int(margin)), max(0, int(gutter))
    usable_w = PAGE_PX_W - 2 * margin - (cols - 1) * gutter
    usable_h = PAGE_PX_H - 2 * margin - (rows - 1) * gutter
    if usable_w < cols * 72 or usable_h < rows * 72:
        raise ValueError("Panel layout does not fit the page.")
    out = []
    for row in range(rows):
        y0 = margin + (usable_h * row) // rows + row * gutter
        y1 = margin + (usable_h * (row + 1)) // rows + row * gutter
        for col in range(cols):
            x0 = margin + (usable_w * col) // cols + col * gutter
            x1 = margin + (usable_w * (col + 1)) // cols + col * gutter
            out.append({"x": x0, "y": y0, "w": x1 - x0,
                        "h": y1 - y0, "border": 9, "_extra": {}})
    return out


def flatten_page(page):
    flat = _surface(True)
    cr = cairo.Context(flat)
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    for layer in page["layers"]:
        if layer.visible:
            cr.set_source_surface(layer.decode(), 0, 0)
            cr.paint_with_alpha(layer.opacity / 100.0)
    if page.get("mask_gutters") and page["panels"]:
        cr.save()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, PAGE_PX_W, PAGE_PX_H)
        for p in page["panels"]:
            cr.rectangle(p["x"], p["y"], p["w"], p["h"])
        cr.set_source_rgb(1, 1, 1)
        cr.fill()
        cr.restore()
    for p in page["panels"]:
        _ring_rect(flat, p["x"], p["y"], p["w"], p["h"], p["border"])
    for bubble in page["bubbles"]:
        raster_bubble(bubble, flat)
    flat.flush()
    return flat


def desaturate(surface):
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, surface.get_width(), surface.get_height())
    cr = cairo.Context(out)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    if hasattr(cairo, "OPERATOR_HSL_SATURATION"):
        cr.set_operator(cairo.OPERATOR_HSL_SATURATION)
        cr.set_source_rgb(0.5, 0.5, 0.5)
        cr.paint()
    else:
        out.flush()
        data, stride = out.get_data(), out.get_stride()
        for y in range(out.get_height()):
            for x in range(out.get_width()):
                i = y * stride + x * 4
                b, g, r, a = data[i:i + 4]
                v = min(255, max(0, int(round(0.114 * b + 0.587 * g + 0.299 * r))))
                data[i:i + 4] = bytes((v, v, v, a))
        out.mark_dirty()
    return out


def _page_order(n):
    """Logical pages with blanks immediately before the positional back cover."""
    if not PAGE_MIN <= int(n) <= PAGE_MAX:
        raise ValueError("page count")
    pages = list(range(1, int(n) + 1))
    pages[-1:-1] = [None] * ((-len(pages)) % 4)
    return pages


def _sheet_pairs(order):
    sides = []
    for fl, fr, bl, br in nbprint._booklet_order(len(order)):
        sides.append([order[fl - 1], order[fr - 1]])
        sides.append([order[bl - 1], order[br - 1]])
    return sides


def cover_pages(order):
    return {order[i] for i in (0, 1, len(order) - 2, len(order) - 1)
            if order[i] is not None}


def _impose(path, order, sheets_filter="all", fold_line=False, draw_page=None):
    """Mirror nbprint's sheet placement while permitting Comics' page remap."""
    pairs = _sheet_pairs(order)
    surf = cairo.PDFSurface(path, nbprint.SHEET_W_PT, nbprint.SHEET_H_PT)
    cr = cairo.Context(surf)
    sheet_count = len(pairs) // 2
    for side_no, pair in enumerate(pairs):
        sheet = side_no // 2 + 1
        if sheets_filter == "cover" and sheet != 1:
            continue
        if sheets_filter == "inside" and sheet == 1:
            continue
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        for slot, page_no in enumerate(pair):
            if page_no is None:
                continue
            cr.save()
            cr.translate(slot * nbprint.HALF_W_PT, 0)
            if draw_page:
                draw_page(cr, page_no, nbprint.HALF_W_PT, nbprint.HALF_H_PT)
            cr.restore()
        if fold_line and sheet == 1 and side_no % 2 == 0:
            cr.set_source_rgb(*nbprint.FOLD_LINE_INK)
            cr.set_line_width(nbprint.FOLD_LINE_W)
            cr.move_to(nbprint.HALF_W_PT, 0)
            cr.line_to(nbprint.HALF_W_PT, nbprint.SHEET_H_PT)
            cr.stroke()
        surf.show_page()
    surf.finish()
    return sheet_count


def draw_flat_page(cr, surface, w=nbprint.HALF_W_PT, h=nbprint.HALF_H_PT):
    cr.save()
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(PRINT_SCALE, PRINT_SCALE)
    pat = cairo.SurfacePattern(surface)
    pat.set_filter(cairo.FILTER_NEAREST)
    cr.set_source(pat)
    cr.paint()
    cr.restore()


def _cached_flatten(cache, pages, index):
    surface = cache.pop(index, None)
    if surface is None:
        surface = flatten_page(pages[index])
    cache[index] = surface
    while len(cache) > 2:
        cache.popitem(last=False)
    return surface


def _place_scale(width, height):
    fit = min(PAGE_PX_W / float(width), PAGE_PX_H / float(height))
    return float(max(1, int(math.floor(fit)))) if fit >= 1 else fit


def load_store(path):
    """Return (document, read_only, reports) without ever rewriting input."""
    damaged = nbapp.preserve_damaged(path)
    if damaged:
        return ComicDocument(), True, ["The recovery file was damaged."]
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return ComicDocument(), False, []
    except Exception:
        return ComicDocument(), True, ["The recovery file could not be read."]
    doc, reports = ComicDocument.parse(raw)
    if doc is None:
        nbapp.quarantine_unrecognized(path)
        return ComicDocument(), True, ["The recovery file was not a Comics document."]
    return doc, False, reports


def save_document(doc, path):
    nbapp.atomic_write_json(path, doc.serial())


def _copy_surface(surface):
    copy_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                      surface.get_width(), surface.get_height())
    cr = cairo.Context(copy_surface)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    copy_surface.flush()
    return copy_surface


def autosave_snapshot(doc):
    """Capture dirty pixels on the UI thread without PNG compression."""
    out = dict(doc._extra)
    out.update({"format": STORE_FORMAT, "app": "comics", "pages": []})
    updates = []
    for page in doc.pages:
        saved = dict(page.get("_extra", {}))
        saved.update({"layers": [],
                      "panels": [dict(x.get("_extra", {}), **{k: x[k] for k in
                                 ("x", "y", "w", "h", "border")}) for x in page["panels"]],
                      "bubbles": [dict(x.get("_extra", {}), **{k: x.get(k) for k in
                                  ("style", "x", "y", "w", "h", "tail", "text",
                                   "size", "align", "bold", "italic")}) for x in page["bubbles"]],
                      "mask_gutters": bool(page.get("mask_gutters", False))})
        for layer in page["layers"]:
            item = dict(layer._extra)
            item.update({"name": layer.name, "visible": layer.visible,
                         "opacity": layer.opacity})
            if layer.dirty:
                surface = _copy_surface(layer.decode())
                item["_surface"] = surface
                updates.append((layer, layer.revision))
            else:
                item["_png"] = layer.encode()
            saved["layers"].append(item)
        out["pages"].append(saved)
    return out, updates


def write_autosave_snapshot(path, snapshot, job=None):
    serial = snapshot
    encoded = []
    for page in serial["pages"]:
        for layer in page["layers"]:
            surface = layer.pop("_surface", None)
            raw = layer.pop("_png", None)
            if surface is not None:
                raw = _png(surface)
            encoded.append(raw)
            layer["png"] = base64.b64encode(raw).decode("ascii")
    if job is not None:
        job.checkpoint()
    nbapp.atomic_write_json(path, serial)
    return encoded


class StackHistory:
    def __init__(self, app):
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
        return _t(names[-1]).rstrip(" \u2026") if names and names[-1] else None

    def undo_label(self):
        return self._top(self._app._undo_names)

    def redo_label(self):
        return self._top(self._app._redo_names)


class Comics(nbapp.AppWindow):
    app_name = "Comics"
    menus = ("File", "Edit", "View", "Page", "Layer")

    def __init__(self, path=None):
        super().__init__()
        self._closed = False
        self.jobs = nbjobs.JobOwner(name="comics")
        self._discarded = False
        # Every armed GLib source lives in this one registry, keyed by name,
        # so destroy-time teardown walks a dict instead of attribute names --
        # and the self_attr audit can still prove no attribute is a callable.
        self._src = {}
        self._fitted = False
        self._prompt_layer = None
        self._save_error = None
        self._recovery_dirty = False
        self._change_generation = 0
        self._store_read_only = False
        self._undo_stack, self._redo_stack = [], []
        self._undo_names, self._redo_names = [], []
        self.history = StackHistory(self)
        self.selection = None
        self.tool = "pencil"
        self.previous_tool = "pencil"
        self.color = "#000000"
        self._recent = []
        try:
            with open(PREFS_FILE) as prefs:
                values=json.load(prefs).get("recent",[])
                self._recent=[x for x in values if isinstance(x,str) and len(x)==7][:16]
        except Exception:
            pass
        self.size = 6
        self.fill_shapes = False
        self.active_layer = 0
        self.page_guides = True
        self._cursor = None
        self._drawing = False
        self._anchor = None
        self._last = None
        self._preview = None
        self._preview_rect = None
        self._pending = None
        self._stroke_track = None
        self._object_before = None
        self._drag_part = "move"
        self._object_overlay = {}
        self._thumb_cache = {}
        self._decoded_pages = []
        self._nudge_before = None
        self.zoom = 1
        self.grid = False
        self.bw_inside = False
        self.doc_path = None
        self._provider = Gtk.CssProvider()
        self._provider.load_from_data(CSS)
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.get_style_context().add_class("comics")
        self.doc, self._store_read_only, reports = load_store(COMICS_FILE)
        if path:
            self._open_path(path)
        self._build_ui()
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)
        self.connect("key-press-event", self._on_key)
        self._refresh()
        if not self._store_read_only:
            # Persist the model NOW (first run seeds, a restore re-affirms),
            # so the chip's first word is a truthful green "Saved" rather
            # than "Unsaved changes" about work the user has not done yet.
            # Novel's rule.
            self._autosave()
        if reports:
            self._flash(reports[0], False)

    def _build_ui(self):
        root = Gtk.Overlay()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row = Gtk.Box()
        dock_sw = Gtk.ScrolledWindow()
        dock_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dock_sw.set_size_request(DOCK_W, -1)
        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        dock.set_border_width(12)
        dock_sw.add(dock)
        lab = self._caption("Tools")
        lab.get_style_context().add_class("comics-group")
        dock.pack_start(lab, False, False, 0)
        grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        self.tool_buttons = {}
        for i, (ident, name, key) in enumerate(TOOLS):
            btn = Gtk.Button(); toolbox=Gtk.Box(spacing=5)
            if ident in nbicons.ICONS: toolicon=nbicons.image(ident,15)
            else:
                toolicon=Gtk.DrawingArea(); toolicon.set_size_request(15,15); toolicon._kind=ident; toolicon.connect("draw",self._draw_tool_mark)
            toolbox.pack_start(toolicon,False,False,0); toolbox.pack_start(Gtk.Label(label=_t(name)),True,True,0); btn.add(toolbox)
            btn.set_tooltip_text(_t(TOOL_HINTS[ident]) + " (" + key + ")")
            btn.connect("clicked", lambda _w, ident=ident: self._set_tool(ident))
            grid.attach(btn, i % 2, i // 2, 1, 1)
            self.tool_buttons[ident] = btn
        dock.pack_start(grid, False, False, 0)
        dock.pack_start(self._caption("Brush size"),False,False,4)
        size_row=Gtk.Box(spacing=4); minus=Gtk.Button(label="-"); plus=Gtk.Button(label="+"); self.size_lbl=Gtk.Label(label=str(self.size)); minus.connect("clicked",lambda *_:self._set_size(self.size-1)); plus.connect("clicked",lambda *_:self._set_size(self.size+1)); size_row.pack_start(minus,False,False,0); size_row.pack_start(self.size_lbl,True,True,0); size_row.pack_start(plus,False,False,0); dock.pack_start(size_row,False,False,0)
        self.ramp_area=Gtk.DrawingArea(); self.ramp_area.set_size_request(-1,30); self.ramp_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK); self.ramp_area.connect("draw",self._draw_ramp); self.ramp_area.connect("button-press-event",self._on_ramp_press); dock.pack_start(self.ramp_area,False,False,0)
        dock.pack_start(self._caption("Shapes"),False,False,4)
        shape_row=Gtk.Box(spacing=4); self.outline_btn=Gtk.ToggleButton(label=_t("Outline")); self.filled_btn=Gtk.ToggleButton(label=_t("Filled")); self.outline_btn.set_active(True); self.outline_btn.connect("clicked",lambda *_:self._set_fill_shapes(False)); self.filled_btn.connect("clicked",lambda *_:self._set_fill_shapes(True)); shape_row.pack_start(self.outline_btn,True,True,0); shape_row.pack_start(self.filled_btn,True,True,0); dock.pack_start(shape_row,False,False,0)
        dock.pack_start(self._caption("Bubble"),False,False,4)
        self.bubble_group=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4); styles=Gtk.Grid(column_spacing=3,row_spacing=3); self.bubble_style="speech"; self.bubble_size=40; self.bubble_bold=False; self.bubble_italic=False
        for i,style in enumerate(("speech","thought","shout","caption")):
            btn=Gtk.Button(label=_t(style.title())); btn.connect("clicked",lambda _w,style=style:self._bubble_setting("style",style)); styles.attach(btn,i%2,i//2,1,1)
        self.bubble_group.pack_start(styles,False,False,0); bramp=Gtk.Box(spacing=2)
        for n in BUBBLE_SIZES:
            btn=Gtk.Button(label=str(n)); btn.set_tooltip_text(_t("%d px")%n); btn.connect("clicked",lambda _w,n=n:self._bubble_setting("size",n)); bramp.pack_start(btn,True,True,0)
        self.bubble_group.pack_start(bramp,False,False,0); fmt=Gtk.Box(spacing=4); bold=Gtk.ToggleButton(label="B"); italic=Gtk.ToggleButton(label="I"); bold.connect("toggled",lambda w:self._bubble_setting("bold",w.get_active())); italic.connect("toggled",lambda w:self._bubble_setting("italic",w.get_active())); fmt.pack_start(bold,False,False,0); fmt.pack_start(italic,False,False,0); self.bubble_group.pack_start(fmt,False,False,0); dock.pack_start(self.bubble_group,False,False,0)
        dock.pack_start(self._caption("Colour"),False,False,4)
        colour_row=Gtk.Box(spacing=8); self.colour_chip=Gtk.DrawingArea(); self.colour_chip.set_size_request(32,24); self.colour_chip.connect("draw",self._draw_colour_chip); self.colour_name=Gtk.Label(label=mix_name(self.color),xalign=0); colour_row.pack_start(self.colour_chip,False,False,0); colour_row.pack_start(self.colour_name,True,True,0); dock.pack_start(colour_row,False,False,0)
        mix=Gtk.Button(label=_t("Mix Colour\u2026")); mix.connect("clicked",lambda *_:self._mix_prompt()); dock.pack_start(mix,False,False,0)
        self.palette_area=Gtk.DrawingArea(); self.palette_area.set_size_request(PAL_COLS*13,7*13); self.palette_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK); self.palette_area.connect("draw",self._draw_palette); self.palette_area.connect("button-press-event",self._on_palette_press); dock.pack_start(self.palette_area,False,False,0)
        dock.pack_start(self._caption("Recent"),False,False,2); self.recent_area=Gtk.DrawingArea(); self.recent_area.set_size_request(PAL_COLS*13,13); self.recent_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK); self.recent_area.connect("draw",self._draw_recent); self.recent_area.connect("button-press-event",self._on_recent_press); dock.pack_start(self.recent_area,False,False,0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(PAGE_PX_W, PAGE_PX_H)
        self.canvas.connect("draw", self._draw_canvas)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                               Gdk.EventMask.BUTTON_RELEASE_MASK |
                               Gdk.EventMask.POINTER_MOTION_MASK |
                               Gdk.EventMask.SCROLL_MASK |
                               Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("scroll-event", self._on_scroll)
        self.canvas.connect("leave-notify-event", self._on_leave)
        self.canvas.connect("realize", self._on_canvas_realize)
        mat = Gtk.ScrolledWindow()
        self.mat = mat
        # first-fit hooks the MAT's allocation: the viewport is the space a
        # fit must fill, and unlike the canvas it does not re-allocate on
        # every zoom step
        self.mat.connect("size-allocate", self._on_first_allocate)
        mat.set_kinetic_scrolling(False)
        mat.set_capture_button_press(False)
        mat.get_style_context().add_class("comics-mat")
        frame = Gtk.Alignment.new(0.5, 0.5, 0, 0)
        frame.set_padding(24, 24, 24, 24)
        frame.add(self.canvas)
        mat.add_with_viewport(frame)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.set_size_request(PANEL_W, -1)
        side.get_style_context().add_class("comics-side")
        phead = Gtk.Box()
        phead.pack_start(self._caption("Pages"), True, True, 8)
        self.page_buttons = {}
        for ident, icon, tip, cb in (("add","plus","Add page",self._add_page),
                                     ("duplicate","duplicate","Duplicate page",self._duplicate_page),
                                     ("delete","trash","Delete page",self._delete_page),
                                     ("up","up","Move page up",lambda: self._move_page(-1)),
                                     ("down","down","Move page down",lambda: self._move_page(1))):
            btn=Gtk.Button(); btn.set_relief(Gtk.ReliefStyle.NONE); btn.add(nbicons.image(icon,15)); btn.set_tooltip_text(_t(tip)); btn.connect("clicked",lambda _w,cb=cb:cb()); phead.pack_start(btn,False,False,0); self.page_buttons[ident]=btn
        side.pack_start(phead, False, False, 0)
        self.pages_box = Gtk.ListBox()
        pages_sw = Gtk.ScrolledWindow()
        pages_sw.add(self.pages_box)
        side.pack_start(pages_sw, True, True, 0)
        self.pages_note = Gtk.Label(xalign=0)
        self.pages_note.set_line_wrap(True)
        side.pack_start(self.pages_note, False, False, 8)
        lhead=Gtk.Box(); lhead.pack_start(self._caption("Layers"),True,True,8)
        self.layer_buttons={}
        for ident,icon,tip,cb in (("up","up","Bring layer forward",lambda:self._move_layer(1)),
                                  ("down","down","Send layer back",lambda:self._move_layer(-1)),
                                  ("add","plus","New layer",self._new_layer),
                                  ("delete","trash","Delete layer",self._delete_layer)):
            btn=Gtk.Button(); btn.set_relief(Gtk.ReliefStyle.NONE); btn.add(nbicons.image(icon,15)); btn.set_tooltip_text(_t(tip)); btn.connect("clicked",lambda _w,cb=cb:cb()); lhead.pack_start(btn,False,False,0); self.layer_buttons[ident]=btn
        side.pack_start(lhead,False,False,0)
        self.layers_box = Gtk.ListBox()
        layer_sw = Gtk.ScrolledWindow()
        layer_sw.set_size_request(-1, 220)
        layer_sw.add(self.layers_box)
        side.pack_end(layer_sw, False, False, 0)
        oprow=Gtk.Box(spacing=8); oprow.pack_start(Gtk.Label(label=_t("Opacity")),False,False,8)
        self.op_val=Gtk.Label(label="100%")
        self.op_scale=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,100,1); self.op_scale.set_draw_value(False); self.op_scale.get_style_context().add_class("opacity")
        self._op_handler=self.op_scale.connect("value-changed",self._on_opacity)
        oprow.pack_start(self.op_scale,True,True,0); oprow.pack_start(self.op_val,False,False,8)
        side.pack_end(oprow,False,False,6)
        row.pack_start(dock_sw, False, False, 0)
        row.pack_start(mat, True, True, 0)
        row.pack_start(side, False, False, 0)
        body.pack_start(row, True, True, 0)
        status = Gtk.Box(spacing=12)
        self.hint_lbl = Gtk.Label(xalign=0)
        self.hint_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.pos_lbl = Gtk.Label(label="")
        self.page_lbl = Gtk.Label()
        self.save_lbl = Gtk.Label()
        status.pack_start(self.hint_lbl, True, True, 12)
        status.pack_start(self.pos_lbl, False, False, 0)
        status.pack_start(self.page_lbl, False, False, 0)
        status.pack_end(self.save_lbl, False, False, 12)
        # The zoom readout IS the control (Illustrator's rule): stepper at the
        # status bar's right, beside the state it reports.
        zrow = Gtk.Box(spacing=3)
        zrow.set_valign(Gtk.Align.CENTER)
        for text, tip, action in (
                ("-", "Zoom out  (-)", lambda *_: self._step_zoom(-1)),):
            b = Gtk.Button(label=text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text(_t(tip))
            b.connect("clicked", action)
            zrow.pack_start(b, False, False, 0)
        self.zoom_lbl = Gtk.Label(label="100%", xalign=0.5)
        self.zoom_lbl.set_size_request(46, -1)
        zrow.pack_start(self.zoom_lbl, False, False, 0)
        for text, tip, action in (
                ("+", "Zoom in  (+)", lambda *_: self._step_zoom(1)),
                ("Fit", "Fit to window  (0)", lambda *_: self._zoom_fit())):
            b = Gtk.Button(label=text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text(_t(tip))
            b.connect("clicked", action)
            zrow.pack_start(b, False, False, 0)
        status.pack_end(zrow, False, False, 0)
        body.pack_end(status, False, False, 8)
        root.add(body)
        # NOT self._overlay: nbapp.AppWindow owns that name (its own
        # chrome overlay, which the launch fade targets) and assigning
        # over it would hand the base class a different widget
        self._prompt_host = root
        self.content.pack_start(root, True, True, 0)
        self.show_all()
        self._new_scratch()
        self._render_chip("unsaved")

    def _caption(self, text):
        label = Gtk.Label(label=_t(text).upper(), xalign=0)
        label.get_style_context().add_class("comics-group")
        return label

    def _draw_tool_mark(self,area,cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE); cr.set_source_rgb(*_rgb("#1A1916")); cr.set_line_width(1.5); kind=area._kind
        if kind=="select":cr.move_to(3,2);cr.line_to(11,8);cr.line_to(7,9);cr.line_to(9,14);cr.line_to(7,15);cr.line_to(5,10);cr.line_to(2,13);cr.close_path()
        elif kind=="bubble":cr.arc(7,6,5,0,2*math.pi);cr.move_to(5,10);cr.line_to(3,14);cr.line_to(8,11)
        else:cr.rectangle(2,3,11,9);cr.move_to(2,8);cr.line_to(13,8);cr.move_to(8,3);cr.line_to(8,8)
        cr.stroke();return False

    def menu_items(self, name):
        if name == "File":
            return [("New    Ctrl+N", self._new), ("Open\u2026    Ctrl+O", self._open),
                    nbapp.SEP, ("Save    Ctrl+S", self._save),
                    ("Save As\u2026    Ctrl+Shift+S", self._save_as), nbapp.SEP,
                    ("Export to PDF\u2026", self._export_prompt),
                    ("Zine Print\u2026", self._print_prompt), nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "Edit":
            items = nbapp.undo_menu_items(self.history)
            return items + [nbapp.SEP, ("Copy Page as Image", self._copy_page),
                            ("Delete", self._delete_selection if self.selection else None)]
        if name == "View":
            tick = lambda on, label: (("\u2713 " if on else "    ") + _t(label))
            return [(tick(self.bw_inside, "Black-and-White Inside"), self._toggle_bw),
                    (tick(self.grid, "Pixel Grid    G"), self._toggle_grid),
                    (tick(self.page_guides, "Page Guides"), self._toggle_guides),
                    nbapp.SEP,
                    ("Zoom In    Ctrl+Plus", lambda: self._step_zoom(1)),
                    ("Zoom Out    Ctrl+Minus", lambda: self._step_zoom(-1)),
                    ("Actual Size    Ctrl+0", lambda: self._set_zoom(1)),
                    ("Fit in Window    Ctrl+9", self._zoom_fit)]
        if name == "Page":
            p = self.doc.pages[self.doc.active]
            bubble = self.selection and self.selection[0] == "bubble"
            return [("New Page", self._add_page if len(self.doc.pages) < PAGE_MAX else None),
                    ("Duplicate Page", self._duplicate_page if len(self.doc.pages) < PAGE_MAX else None),
                    ("Delete Page", self._delete_page if len(self.doc.pages) > PAGE_MIN else None),
                    ("Move Page Up", (lambda: self._move_page(-1)) if self.doc.active else None),
                    ("Move Page Down", (lambda: self._move_page(1)) if self.doc.active < len(self.doc.pages) - 1 else None),
                    nbapp.SEP, ("Panel Layout\u2026", self._panel_layout_prompt),
                    (("\u2713 " if p["mask_gutters"] else "    ") + _t("Hide Art Outside Panels"), self._toggle_mask),
                    ("Place Image\u2026", self._place_image), nbapp.SEP,
                    ("Bring Bubble Forward", self._bubble_forward if bubble else None),
                    ("Send Bubble Backward", self._bubble_backward if bubble else None)]
        if name == "Layer":
            p = self.doc.pages[self.doc.active]
            return [("New Layer", self._new_layer if len(p["layers"]) < LAYER_MAX else None),
                    ("Delete Layer", self._delete_layer if len(p["layers"]) > 1 else None),
                    ("Clear Layer", self._clear_layer)]
        return super().menu_items(name)

    def _snapshot(self):
        return self.doc.bytes(), self.doc.active

    def _restore_snapshot(self, snap):
        raw, active = snap
        doc, _errors = ComicDocument.parse(json.loads(raw.decode("utf-8")))
        self.doc = doc
        self.doc.active = min(active, len(doc.pages) - 1)
        self.active_layer = max(0, min(self.active_layer,
                                      len(doc.pages[self.doc.active]["layers"]) - 1))
        self._object_overlay.clear()
        self._thumb_cache.clear()
        self._refresh()

    def _push(self, before, name):
        frame = before if isinstance(before, tuple) and before and before[0] in ("px", "st") else ("st", before)
        self._undo_stack.append(frame)
        if frame[0] == "st":
            self._object_overlay.pop(self.doc.active, None)
            self._thumb_cache.pop(self.doc.active, None)
        self._undo_names.append(name)
        self._redo_stack.clear()
        self._redo_names.clear()
        while len(self._undo_stack) > UNDO_DEPTH or (len(self._undo_stack) > 1 and self._history_bytes() > HISTORY_BYTES):
            self._undo_stack.pop(0)
            self._undo_names.pop(0)
        self._changed()

    def _undo(self):
        if not self._undo_stack:
            return False
        frame = self._undo_stack.pop()
        name = self._undo_names.pop()
        inverse = self._apply_history(frame)
        self._redo_stack.append(inverse)
        self._redo_names.append(name)
        return True

    def _redo(self):
        if not self._redo_stack:
            return False
        frame = self._redo_stack.pop()
        name = self._redo_names.pop()
        inverse = self._apply_history(frame)
        self._undo_stack.append(inverse)
        self._undo_names.append(name)
        return True

    def _history_bytes(self):
        total = 0
        for frame in self._undo_stack + self._redo_stack:
            if frame[0] == "px":
                total += len(frame[7]) + len(frame[8])
            elif frame[0] == "st":
                total += len(frame[1][0])
        return total

    def _apply_history(self, frame):
        if frame[0] == "st":
            inverse = ("st", self._snapshot())
            self._restore_snapshot(frame[1])
            return inverse
        _kind, page_i, layer_i, x, y, w, h, before, after = frame
        if self.doc.active != page_i:
            self._switch_page(page_i)
        page = self.doc.pages[page_i]
        layer_i = max(0, min(layer_i, len(page["layers"]) - 1))
        surface = page["layers"][layer_i].decode()
        current = self._rect_bytes(surface, (x, y, w, h))
        self._put_rect_bytes(surface, (x, y, w, h), before)
        page["layers"][layer_i].touch()
        self.active_layer = layer_i
        self._touch_page(page_i, objects=False)
        self._refresh()
        return ("px", page_i, layer_i, x, y, w, h, current, before)

    @staticmethod
    def _rect_bytes(surface, rect):
        x, y, w, h = rect
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        return b"".join(bytes(data[(y + row) * stride + x * 4:
                                   (y + row) * stride + (x + w) * 4])
                        for row in range(h))

    @staticmethod
    def _put_rect_bytes(surface, rect, raw):
        x, y, w, h = rect
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        for row in range(h):
            start = row * w * 4
            data[(y + row) * stride + x * 4:
                 (y + row) * stride + (x + w) * 4] = raw[start:start + w * 4]
        surface.mark_dirty()

    def _begin_edit(self):
        page_i = self.doc.active
        layer_i = max(0, min(self.active_layer,
                             len(self.doc.pages[page_i]["layers"]) - 1))
        surface = self.doc.pages[page_i]["layers"][layer_i].decode()
        self._pending = (page_i, layer_i,
                         self._rect_bytes(surface, (0, 0, PAGE_PX_W, PAGE_PX_H)))

    def _commit_edit(self, region=None, name=None):
        pending, self._pending = self._pending, None
        if pending is None:
            return False
        page_i, layer_i, whole = pending
        x, y, w, h = self._clamp_rect(region or (0, 0, PAGE_PX_W, PAGE_PX_H))
        if not w or not h:
            return False
        surface = self.doc.pages[page_i]["layers"][layer_i].decode()
        after = self._rect_bytes(surface, (x, y, w, h))
        before = b"".join(whole[(y + row) * PAGE_PX_W * 4 + x * 4:
                                (y + row) * PAGE_PX_W * 4 + (x + w) * 4]
                          for row in range(h))
        if before == after:
            return False
        self.doc.pages[page_i]["layers"][layer_i].touch()
        self._push(("px", page_i, layer_i, x, y, w, h, before, after), name)
        self._touch_page(page_i, objects=False)
        return True

    @staticmethod
    def _clamp_rect(rect):
        x, y, w, h = map(int, rect)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(PAGE_PX_W, x + w), min(PAGE_PX_H, y + h)
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    @staticmethod
    def _union_rect(a, b):
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1 = max(a[0] + a[2], b[0] + b[2])
        y1 = max(a[1] + a[3], b[1] + b[3])
        return x0, y0, x1 - x0, y1 - y0

    def _changed(self):
        self._change_generation += 1
        self._recovery_dirty = True
        self._render_chip("unsaved")
        self._cancel_source("save")
        self._src["save"] = GLib.timeout_add(2500, self._autosave)
        self.canvas.queue_draw()

    def _autosave(self):
        self._src.pop("save", None)
        if self._closed:
            return False
        if self._store_read_only:
            return False
        snapshot, updates = autosave_snapshot(self.doc)
        generation = self._change_generation

        def work(job):
            job.checkpoint()
            return write_autosave_snapshot(COMICS_FILE, snapshot, job)

        def done(encoded):
            if self._closed:
                return
            for (layer, revision), raw in zip(updates, encoded):
                if layer.revision == revision:
                    layer.png = raw
                    layer.dirty = False
            self._save_error = None
            self._recovery_dirty = self._change_generation != generation
            self._render_chip("unsaved" if self._recovery_dirty else "saved")

        def failed(error):
            if self._closed:
                return
            self._save_error = error
            self._recovery_dirty = True
            self._flash(_t(nbapp.save_failure_reason(error, COMICS_FILE)), False)

        self.jobs.start("autosave", work, on_done=done, on_error=failed,
                        policy=nbjobs.REPLACE)
        return False

    def _save_state_sync(self):
        if self._store_read_only:
            return False
        self.jobs.cancel("autosave")
        self.jobs.join()
        try:
            save_document(self.doc, COMICS_FILE)
        except Exception as exc:
            self._save_error = exc
            self._recovery_dirty = True
            return False
        self._save_error = None
        self._recovery_dirty = False
        self._render_chip("saved")
        return True

    def _refresh(self):
        if not hasattr(self, "pages_box"):
            return
        for box in (self.pages_box, self.layers_box):
            for child in box.get_children():
                box.remove(child)
        total = len(self.doc.pages)
        for i in range(total):
            row = Gtk.ListBoxRow()
            box=Gtk.Box(spacing=8); thumb=Gtk.DrawingArea(); thumb.set_size_request(96,148); thumb._page_i=i; thumb.connect("draw",self._draw_thumbnail)
            box.pack_start(thumb,False,False,6)
            text=Gtk.Label(label=self._page_name(i),xalign=0); text.set_ellipsize(Pango.EllipsizeMode.END); box.pack_start(text,True,True,0); row.add(box)
            row.connect("button-press-event", lambda _w, _e, i=i: self._switch_page(i))
            self.pages_box.add(row)
            if i == self.doc.active:
                self.pages_box.select_row(row)
        page=self.doc.pages[self.doc.active]
        for index in reversed(range(len(page["layers"]))):
            ly=page["layers"][index]; row=Gtk.ListBoxRow(); box=Gtk.Box(spacing=6)
            eye=Gtk.ToggleButton(); eye.set_active(ly.visible); eye.add(nbicons.image("eye" if ly.visible else "eyeoff",15)); eye.connect("toggled",lambda w,index=index:self._toggle_layer(index,w.get_active())); box.pack_start(eye,False,False,0)
            box.pack_start(Gtk.Label(label=ly.name,xalign=0),True,True,0); box.pack_start(Gtk.Label(label="%d%%"%ly.opacity),False,False,6); row.add(box); row.connect("button-press-event",lambda _w,_e,index=index:self._select_layer(index)); self.layers_box.add(row)
            if index==self.active_layer:self.layers_box.select_row(row)
        if len(page["layers"])==1:
            hint=Gtk.ListBoxRow(); hint.set_sensitive(False)
            hint_lbl=Gtk.Label(label=_t("Add a layer to draw over the Background without changing it."),xalign=0)
            hint_lbl.set_line_wrap(True); hint_lbl.set_max_width_chars(24)
            hint.add(hint_lbl); self.layers_box.add(hint)
        self.hint_lbl.set_text(_t(TOOL_HINTS[self.tool]))
        self.page_lbl.set_text(_t("Page %d of %d") % (self.doc.active + 1, total))
        sheets=(total+3)//4; blanks=(-total)%4
        note=_t("Letter sheets: %d")%sheets
        if blanks==1: note+="\n"+_t("One page prints blank before the back cover.")
        elif blanks: note+="\n"+(_t("%d pages print blank before the back cover.")%blanks)
        self.pages_note.set_text(note)
        self.page_buttons["add"].set_sensitive(total<PAGE_MAX); self.page_buttons["duplicate"].set_sensitive(total<PAGE_MAX); self.page_buttons["delete"].set_sensitive(total>PAGE_MIN); self.page_buttons["up"].set_sensitive(self.doc.active>0); self.page_buttons["down"].set_sensitive(self.doc.active<total-1)
        self.layer_buttons["add"].set_sensitive(len(page["layers"])<LAYER_MAX); self.layer_buttons["delete"].set_sensitive(len(page["layers"])>1); self.layer_buttons["up"].set_sensitive(self.active_layer<len(page["layers"])-1); self.layer_buttons["down"].set_sensitive(self.active_layer>0)
        self.op_scale.handler_block(self._op_handler); self.op_scale.set_value(page["layers"][self.active_layer].opacity); self.op_scale.handler_unblock(self._op_handler); self.op_val.set_text("%d%%"%page["layers"][self.active_layer].opacity)
        self.pages_box.show_all()
        self.layers_box.show_all()
        self.canvas.queue_draw()
        if self.doc_path:
            self.set_title("Comics - " + os.path.basename(self.doc_path))

    def _draw_thumbnail(self, widget, cr):
        surface=self._thumbnail(widget._page_i); cr.save(); cr.scale(widget.get_allocated_width()/96.0,widget.get_allocated_height()/148.0); cr.set_source_surface(surface,0,0); cr.get_source().set_filter(cairo.FILTER_BILINEAR); cr.paint(); cr.restore()
        # a hairline so an empty (white) page still reads as a page card
        cr.set_source_rgb(*_rgb("#C9C4B6")); cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, widget.get_allocated_width()-1, widget.get_allocated_height()-1); cr.stroke()
        return False

    def _select_layer(self,index):
        self.active_layer=index; self._refresh(); return True

    def _toggle_layer(self,index,visible):
        before=self._snapshot(); self.doc.pages[self.doc.active]["layers"][index].visible=visible; self._push(before,"Layer Visibility"); self._touch_page(self.doc.active,objects=False); self._refresh()

    def _on_opacity(self,scale):
        page=self.doc.pages[self.doc.active]; value=int(scale.get_value())
        if page["layers"][self.active_layer].opacity==value:return
        before=self._snapshot(); page["layers"][self.active_layer].opacity=value; self._push(before,"Layer Opacity"); self._touch_page(self.doc.active,objects=False); self._refresh()

    def _move_layer(self,delta):
        page=self.doc.pages[self.doc.active]; to=self.active_layer+delta
        if not 0<=to<len(page["layers"]):return False
        before=self._snapshot(); page["layers"][self.active_layer],page["layers"][to]=page["layers"][to],page["layers"][self.active_layer]; self.active_layer=to; self._push(before,"Move Layer"); self._touch_page(self.doc.active,objects=False); self._refresh(); return True

    def _page_name(self, i):
        n = len(self.doc.pages)
        special = {0: "Front cover", 1: "Inside front", n - 2: "Inside back", n - 1: "Back cover"}
        text = _t(special[i]) if i in special else _t("Page %d") % (i + 1)
        return "%d  %s" % (i + 1, text)

    def _switch_page(self, index):
        if 0 <= index < len(self.doc.pages):
            leaving = self.doc.active
            self._cache_page_switch(leaving, index)
            self.doc.active = index
            self.active_layer = min(self.active_layer,
                                    len(self.doc.pages[index]["layers"]) - 1)
            self.selection = None
            self._refresh()
        return True

    def _cache_page_switch(self, leaving, entering):
        keep = {leaving, entering}
        for i, page in enumerate(self.doc.pages):
            if i in keep:
                for layer in page["layers"]:
                    layer.decode()
                continue
            for layer in page["layers"]:
                if layer.surface is not None:
                    layer.encode()
                    layer.surface = None
        self._decoded_pages = sorted(keep)

    def _touch_page(self, page_i, objects=True):
        if objects:
            self._object_overlay.pop(page_i, None)
        self._thumb_cache.pop(page_i, None)
        self._cancel_source("thumb")
        self._src["thumb"] = GLib.timeout_add(600, self._thumb_ready, page_i)
        self._changed()

    def _thumb_ready(self, page_i):
        self._src.pop("thumb", None)
        if self._closed:
            return False
        self._thumbnail(page_i)
        return False

    def _thumbnail(self, page_i):
        cached = self._thumb_cache.get(page_i)
        if cached is not None:
            return cached
        thumb = cairo.ImageSurface(cairo.FORMAT_ARGB32, 96, 148)
        cr = cairo.Context(thumb)
        cr.scale(96.0 / PAGE_PX_W, 148.0 / PAGE_PX_H)
        pat = cairo.SurfacePattern(flatten_page(self.doc.pages[page_i]))
        pat.set_filter(cairo.FILTER_BILINEAR)
        cr.set_source(pat); cr.paint()
        self._thumb_cache[page_i] = thumb
        return thumb

    def _on_canvas_realize(self, widget):
        win = widget.get_window()
        win.set_event_compression(True)
        try:
            win.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "crosshair"))
        except Exception:
            pass

    def _on_first_allocate(self, _widget, _allocation):
        # ONCE: without the flag every canvas re-allocation (each zoom step
        # re-requests the canvas size) re-armed a fit and snapped the user's
        # chosen zoom straight back.
        if not self._fitted and "fit" not in self._src:
            self._fitted = True
            self._src["fit"] = GLib.idle_add(self._fit_once)

    def _fit_once(self):
        self._src.pop("fit", None)
        if self._closed:
            return False
        self._zoom_fit()
        return False

    def _zoom_fit(self):
        # Fit against the MAT (the scrolled viewport), never the canvas'
        # own frame: the frame grows with the canvas, so measuring it made
        # every fit a no-op at whatever zoom was already set.
        alloc = self.mat.get_allocation()
        self._set_zoom(fit_zoom(PAGE_PX_W, PAGE_PX_H,
                                max(1, alloc.width - 48), max(1, alloc.height - 48)))

    def _new_scratch(self):
        self._scratch = _surface(False)
        self._preview = None
        self._preview_rect = None

    def _clear_scratch(self):
        self._scratch = _surface(False)
        self._preview = None
        self._preview_rect = None

    def _active_surface(self):
        page = self.doc.pages[self.doc.active]
        self.active_layer = max(0, min(self.active_layer, len(page["layers"]) - 1))
        return page["layers"][self.active_layer].decode()

    def _paint_stamp(self, surface, x, y, erase=False):
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        value = CLEAR4 if erase else px4(self.color)
        shape = "round" if self.tool == "brush" else "square"
        for dy, xa, xb in brush_runs(self.size, shape):
            yy, xa, xb = y + dy, x + xa, x + xb
            if yy < 0 or yy >= PAGE_PX_H:
                continue
            xa, xb = max(0, xa), min(PAGE_PX_W - 1, xb)
            if xb >= xa:
                data[yy * stride + xa * 4:yy * stride + (xb + 1) * 4] = value * (xb - xa + 1)
        surface.mark_dirty()

    def _stroke_segment(self, a, b):
        surface = self._active_surface()
        for x, y in _line_points(a[0], a[1], b[0], b[1]):
            self._paint_stamp(surface, x, y, self.tool == "eraser")
        pad = self.size // 2 + 1
        rect = (min(a[0], b[0]) - pad, min(a[1], b[1]) - pad,
                abs(a[0] - b[0]) + pad * 2 + 1,
                abs(a[1] - b[1]) + pad * 2 + 1)
        rect = self._clamp_rect(rect)
        self._stroke_track = rect if self._stroke_track is None else self._union_rect(self._stroke_track, rect)

    def _shape_ops(self, tool, a, b):
        x0, x1 = sorted((a[0], b[0])); y0, y1 = sorted((a[1], b[1]))
        if tool == "line":
            return _line_points(a[0], a[1], b[0], b[1]), []
        if tool == "rect":
            if self.fill_shapes:
                return [], [(y, x0, x1) for y in range(y0, y1 + 1)]
            pts = []
            for c, d in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                         ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                pts.extend(_line_points(c[0], c[1], d[0], d[1]))
            return pts, []
        spans = _ellipse_spans(x0, y0, x1, y1)
        return ([], spans) if self.fill_shapes else (_ellipse_outline(spans), [])

    def _render_shape(self, surface, tool, a, b):
        pts, spans = self._shape_ops(tool, a, b)
        for x, y in pts:
            self._paint_stamp(surface, x, y)
        _spans(surface, spans, self.color)
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return self._clamp_rect((min(xs) - self.size, min(ys) - self.size,
                                     max(xs) - min(xs) + self.size * 2 + 1,
                                     max(ys) - min(ys) + self.size * 2 + 1))
        return self._clamp_rect((min(a[0], b[0]), min(a[1], b[1]),
                                 abs(a[0] - b[0]) + 1, abs(a[1] - b[1]) + 1))

    def _preview_shape(self, a, b):
        self._clear_scratch()
        self._preview_rect = self._render_shape(self._scratch, self.tool, a, b)
        self._preview = (self.tool, a, b)
        self.canvas.queue_draw()

    def _flood_fill(self, point):
        surface = self._active_surface(); surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        px, py = point; start = py * stride + px * 4
        target, replacement = bytes(data[start:start + 4]), px4(self.color)
        if target == replacement:
            return False
        self._begin_edit()
        chunks, n = {}, 1
        while n <= PAGE_PX_W:
            chunks[n] = target * n
            n *= 2

        def extent_right(row, x):
            pos, step = x, 1
            while pos + step <= PAGE_PX_W:
                off = row + pos * 4
                if bytes(data[off:off + step * 4]) != chunks[step]:
                    break
                pos += step; step *= 2
            lo, hi = 0, min(step - 1, PAGE_PX_W - pos)
            while lo < hi:
                mid = (lo + hi + 1) // 2; off = row + pos * 4
                if bytes(data[off:off + mid * 4]) == target * mid: lo = mid
                else: hi = mid - 1
            return pos + lo - 1

        def extent_left(row, x):
            pos, step = x + 1, 1
            while pos - step >= 0:
                off = row + (pos - step) * 4
                if bytes(data[off:off + step * 4]) != chunks[step]:
                    break
                pos -= step; step *= 2
            lo, hi = 0, min(step - 1, pos)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                off = row + (pos - mid) * 4
                if bytes(data[off:off + mid * 4]) == target * mid: lo = mid
                else: hi = mid - 1
            return pos - lo

        stack = [(px, py)]; seeds = 1
        while stack:
            x, y = stack.pop(); row = y * stride; at = row + x * 4
            if bytes(data[at:at + 4]) != target:
                continue
            left, right = extent_left(row, x), extent_right(row, x)
            data[row + left * 4:row + (right + 1) * 4] = replacement * (right - left + 1)
            for ny in (y - 1, y + 1):
                if not 0 <= ny < PAGE_PX_H:
                    continue
                nrow = ny * stride; begin = nrow + left * 4
                end = nrow + (right + 1) * 4; scan = begin
                while scan < end:
                    hit = bytes(data[scan:end]).find(target)
                    if hit < 0:
                        break
                    found = scan + hit
                    aligned = found - ((found - nrow) % 4)
                    if bytes(data[aligned:aligned + 4]) != target:
                        scan = found + 1
                        continue
                    sx = (aligned - nrow) // 4
                    stack.append((sx, ny)); seeds += 1
                    run_right = extent_right(nrow, sx)
                    scan = nrow + (run_right + 1) * 4
        self._fill_seed_count = seeds
        surface.mark_dirty()
        return self._commit_edit(None, "Fill")

    def _pick_colour(self, point):
        out_r = out_g = out_b = 1.0
        x, y = point
        for layer in self.doc.pages[self.doc.active]["layers"]:
            if layer.visible:
                surface = layer.decode(); surface.flush()
                data, stride = surface.get_data(), surface.get_stride()
                i = y * stride + x * 4
                b, g, r, a = data[i:i + 4]
                opacity = layer.opacity / 100.0
                alpha = (a / 255.0) * opacity
                out_r = (r / 255.0) * opacity + out_r * (1.0 - alpha)
                out_g = (g / 255.0) * opacity + out_g * (1.0 - alpha)
                out_b = (b / 255.0) * opacity + out_b * (1.0 - alpha)
        self.color = "#%02X%02X%02X" % (round(out_r * 255),
                                         round(out_g * 255), round(out_b * 255))
        self._set_tool(self.previous_tool)

    def _hit_object(self, point):
        x, y = point; page = self.doc.pages[self.doc.active]
        for i in range(len(page["bubbles"]) - 1, -1, -1):
            b = page["bubbles"][i]
            if b["x"] <= x < b["x"] + b["w"] and b["y"] <= y < b["y"] + b["h"]:
                return "bubble", i
        for i in range(len(page["panels"]) - 1, -1, -1):
            p = page["panels"][i]
            if p["x"] <= x < p["x"] + p["w"] and p["y"] <= y < p["y"] + p["h"]:
                return "panel", i
        return None

    def _selection_part(self, point):
        if not self.selection:return "move"
        kind,index=self.selection; obj=self.doc.pages[self.doc.active]["bubbles" if kind=="bubble" else "panels"][index]; x,y=point
        handle_tolerance = max(4, int(round(8 / self.zoom)))
        tail_tolerance = max(6, int(round(10 / self.zoom)))
        if kind=="bubble" and obj.get("tail") and abs(x-obj["tail"][0])<=tail_tolerance and abs(y-obj["tail"][1])<=tail_tolerance:return "tail"
        positions=((obj["x"],obj["y"],"nw"),(obj["x"]+obj["w"]//2,obj["y"],"n"),(obj["x"]+obj["w"],obj["y"],"ne"),(obj["x"],obj["y"]+obj["h"]//2,"w"),(obj["x"]+obj["w"],obj["y"]+obj["h"]//2,"e"),(obj["x"],obj["y"]+obj["h"],"sw"),(obj["x"]+obj["w"]//2,obj["y"]+obj["h"],"s"),(obj["x"]+obj["w"],obj["y"]+obj["h"],"se"))
        for hx,hy,name in positions:
            if abs(x-hx)<=handle_tolerance and abs(y-hy)<=handle_tolerance:return name
        return "move"

    def _on_press(self, _widget, ev):
        if getattr(ev, "button", 1) != 1:
            return False
        p = view_pixel(ev.x, ev.y, self.zoom, clamp=True)
        self._cursor = p
        if self.tool in ("pencil", "brush", "eraser"):
            self._drawing = True; self._anchor = self._last = p; self._stroke_track = None
            self._begin_edit(); self._stroke_segment(p, p); self.canvas.queue_draw(); return True
        if self.tool in ("line", "rect", "ellipse", "panel"):
            self._drawing = True; self._anchor = self._last = p
            if self.tool != "panel": self._begin_edit()
            return True
        if self.tool == "fill": self._flood_fill(p); self.canvas.queue_draw(); return True
        if self.tool == "picker": self._pick_colour(p); self.canvas.queue_draw(); return True
        hit = self._hit_object(p)
        if self.tool == "bubble":
            if hit and hit[0] == "bubble":
                self.selection = hit; self._bubble_editor(hit[1]); return True
            before = self._snapshot(); b = _bubble_defaults(p[0], p[1]); b.update(style=self.bubble_style,size=self.bubble_size,bold=self.bubble_bold,italic=self.bubble_italic)
            self.doc.pages[self.doc.active]["bubbles"].append(b)
            self.selection = ("bubble", len(self.doc.pages[self.doc.active]["bubbles"]) - 1)
            self._bubble_editor(self.selection[1], new_before=before); self._touch_page(self.doc.active); return True
        if self.tool == "select":
            old=self.selection
            if old and self._selection_part(p)!="move":hit=old
            self.selection = hit
            if hit:
                self._drawing = True; self._anchor = p; self._last = p; self._object_before = self._snapshot(); self._drag_part=self._selection_part(p)
                if getattr(ev, "type", None) == Gdk.EventType._2BUTTON_PRESS and hit[0] == "bubble": self._bubble_editor(hit[1])
            self.canvas.queue_draw(); return True
        return False

    def _on_motion(self, _widget, ev):
        p = view_pixel(ev.x, ev.y, self.zoom, clamp=True); self._cursor = p
        self.pos_lbl.set_text("%d, %d" % p)
        if not self._drawing:
            self.canvas.queue_draw(); return True
        shift = bool(getattr(ev, "state", 0) & Gdk.ModifierType.SHIFT_MASK)
        if self.tool in ("pencil", "brush", "eraser"):
            self._stroke_segment(self._last, p); self._last = p
        elif self.tool in ("line", "rect", "ellipse"):
            end = _snap45(self._anchor, p) if self.tool == "line" and shift else (_square(self._anchor, p) if shift else p)
            self._last = end; self._preview_shape(self._anchor, end)
            self.pos_lbl.set_text(self._dims(abs(end[0] - self._anchor[0]) + 1,
                                             abs(end[1] - self._anchor[1]) + 1))
        elif self.tool == "panel":
            self._last = _square(self._anchor, p) if shift else p; self.canvas.queue_draw()
        elif self.tool == "select" and self.selection:
            seq = self.doc.pages[self.doc.active]["bubbles" if self.selection[0] == "bubble" else "panels"]
            obj = seq[self.selection[1]]; dx, dy = p[0] - self._last[0], p[1] - self._last[1]
            if self._drag_part=="tail":obj["tail"]=[p[0],p[1]]
            elif self._drag_part=="move":
                obj["x"] = max(0, min(PAGE_PX_W - obj["w"], obj["x"] + dx)); obj["y"] = max(0, min(PAGE_PX_H - obj["h"], obj["y"] + dy))
                if obj.get("tail") is not None: obj["tail"] = [obj["tail"][0] + dx, obj["tail"][1] + dy]
            else:
                minw,minh=(BUBBLE_MIN_W,BUBBLE_MIN_H) if self.selection[0]=="bubble" else (72,72)
                if "e" in self._drag_part:obj["w"]=max(minw,min(PAGE_PX_W-obj["x"],obj["w"]+dx))
                if "s" in self._drag_part:obj["h"]=max(minh,min(PAGE_PX_H-obj["y"],obj["h"]+dy))
                if "w" in self._drag_part:
                    right=obj["x"]+obj["w"]; obj["x"]=max(0,min(right-minw,obj["x"]+dx)); obj["w"]=right-obj["x"]
                if "n" in self._drag_part:
                    bottom=obj["y"]+obj["h"]; obj["y"]=max(0,min(bottom-minh,obj["y"]+dy)); obj["h"]=bottom-obj["y"]
                if self.selection[0]=="bubble":grow_bubble(obj)
            self._last = p; self._object_overlay.pop(self.doc.active, None); self.canvas.queue_draw()
        return True

    def _on_release(self, _widget, ev):
        if not self._drawing:
            return False
        p = view_pixel(ev.x, ev.y, self.zoom, clamp=True); self._drawing = False
        if self.tool in ("pencil", "brush", "eraser"):
            self._stroke_segment(self._last, p); self._commit_edit(self._stroke_track, self.tool.title())
        elif self.tool in ("line", "rect", "ellipse"):
            end = self._last; rect = self._render_shape(self._active_surface(), self.tool, self._anchor, end)
            self._clear_scratch(); self._commit_edit(rect, self.tool.title())
        elif self.tool == "panel":
            x0, x1 = sorted((self._anchor[0], self._last[0])); y0, y1 = sorted((self._anchor[1], self._last[1]))
            if x1 - x0 + 1 >= 72 and y1 - y0 + 1 >= 72:
                before = self._snapshot(); page = self.doc.pages[self.doc.active]
                page["panels"].append({"x": x0, "y": y0, "w": x1 - x0 + 1, "h": y1 - y0 + 1, "border": 9, "_extra": {}})
                self.selection = ("panel", len(page["panels"]) - 1); self._push(before, "Add Panel"); self._touch_page(self.doc.active)
        elif self.tool == "select" and self._object_before:
            self._push(self._object_before, "Move Bubble" if self.selection[0] == "bubble" else "Move Panel")
            self._object_before = None; self._touch_page(self.doc.active)
        self.canvas.queue_draw(); return True

    def _on_scroll(self, _widget, ev):
        if getattr(ev, "state", 0) & Gdk.ModifierType.CONTROL_MASK:
            old=self.zoom; hx,hy=view_pixel(ev.x,ev.y,old)
            self._step_zoom(-1 if ev.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.LEFT) else 1)
            hadj,vadj=self.mat.get_hadjustment(),self.mat.get_vadjustment(); hadj.set_value(max(hadj.get_lower(),min(hadj.get_upper()-hadj.get_page_size(),hx*self.zoom-ev.x))); vadj.set_value(max(vadj.get_lower(),min(vadj.get_upper()-vadj.get_page_size(),hy*self.zoom-ev.y)))
            return True
        return False

    def _on_leave(self, *_args):
        self._cursor = None; self.pos_lbl.set_text(""); self.canvas.queue_draw(); return False

    @staticmethod
    def _dims(w, h):
        return "\u2066%d \u00d7 %d\u2069" % (w, h)

    def _draw_canvas(self, _widget, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        z = self.zoom; page = self.doc.pages[self.doc.active]
        # No view-composite cache: cairo scaling cost is destination-bound.
        cr.save(); cr.scale(z, z); cr.set_source_rgb(1, 1, 1); cr.paint()
        for layer in page["layers"]:
            if layer.visible:
                cr.set_source_surface(layer.decode(), 0, 0)
                cr.get_source().set_filter(cairo.FILTER_NEAREST if z >= 1 else cairo.FILTER_BILINEAR)
                cr.paint_with_alpha(layer.opacity / 100.0)
        if page.get("mask_gutters") and page["panels"]:
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD); cr.rectangle(0, 0, PAGE_PX_W, PAGE_PX_H)
            for panel in page["panels"]: cr.rectangle(panel["x"], panel["y"], panel["w"], panel["h"])
            cr.set_source_rgb(1, 1, 1); cr.fill()
        overlay = self._object_surface(self.doc.active)
        cr.set_source_surface(overlay, 0, 0); cr.get_source().set_filter(cairo.FILTER_NEAREST); cr.paint()
        if self._preview is not None:
            cr.set_source_surface(self._scratch, 0, 0); cr.get_source().set_filter(cairo.FILTER_NEAREST); cr.paint()
        if self.bw_inside and self.doc.active not in (0, 1, len(self.doc.pages) - 2, len(self.doc.pages) - 1) and hasattr(cairo, "OPERATOR_HSL_SATURATION"):
            cr.set_operator(cairo.OPERATOR_HSL_SATURATION); cr.set_source_rgb(0.5, 0.5, 0.5); cr.paint(); cr.set_operator(cairo.OPERATOR_OVER)
        cr.restore()
        if self.page_guides:
            cr.set_source_rgba(*_rgb("#9A9484"), 0.75); cr.set_line_width(1)
            cr.rectangle(75 * z + 0.5, 75 * z + 0.5,
                         (PAGE_PX_W - 150) * z, (PAGE_PX_H - 150) * z); cr.stroke()
        if self.grid and z >= GRID_FROM:
            cr.set_source_rgba(*_rgb("#C9C4B6"), 0.45); cr.set_line_width(1)
            for x in range(1, PAGE_PX_W): cr.move_to(x * z + 0.5, 0); cr.line_to(x * z + 0.5, PAGE_PX_H * z)
            for y in range(1, PAGE_PX_H): cr.move_to(0, y * z + 0.5); cr.line_to(PAGE_PX_W * z, y * z + 0.5)
            cr.stroke()
        self._draw_selection(cr, z)
        self._draw_brush_cursor(cr, z)
        return False

    def _object_surface(self, page_i):
        cached = self._object_overlay.get(page_i)
        if cached is not None:
            return cached
        page = self.doc.pages[page_i]; surface = _surface(False)
        for panel in page["panels"]:
            _ring_rect(surface, panel["x"], panel["y"], panel["w"], panel["h"], panel["border"])
        for bubble in page["bubbles"]: raster_bubble(bubble, surface)
        self._object_overlay[page_i] = surface
        return surface

    def _draw_selection(self, cr, z):
        if not self.selection:
            if self.tool == "panel" and self._drawing and self._anchor and self._last:
                x0, x1 = sorted((self._anchor[0], self._last[0])); y0, y1 = sorted((self._anchor[1], self._last[1]))
                cr.set_source_rgb(*_rgb("#C8341E")); cr.set_line_width(1); cr.rectangle(x0 * z + .5, y0 * z + .5, (x1-x0+1)*z, (y1-y0+1)*z); cr.stroke()
            return
        kind, index = self.selection; seq = self.doc.pages[self.doc.active]["bubbles" if kind == "bubble" else "panels"]
        if not 0 <= index < len(seq): return
        obj = seq[index]; x, y, w, h = obj["x"]*z, obj["y"]*z, obj["w"]*z, obj["h"]*z
        cr.set_source_rgb(*_rgb("#C8341E")); cr.set_line_width(1); cr.rectangle(x+.5, y+.5, w, h); cr.stroke()
        # Selection chrome is authored in screen pixels, so fit zoom does not
        # turn its handles and hairline into sub-pixel dust.
        d = 7
        for hx, hy in ((x,y),(x+w/2,y),(x+w,y),(x,y+h/2),(x+w,y+h/2),(x,y+h),(x+w/2,y+h),(x+w,y+h)):
            cr.rectangle(hx-d/2, hy-d/2, d, d); cr.fill()
        if kind == "bubble" and obj.get("tail"):
            tx, ty = obj["tail"][0]*z, obj["tail"][1]*z
            cr.move_to(tx, ty-d); cr.line_to(tx+d, ty); cr.line_to(tx, ty+d); cr.line_to(tx-d, ty); cr.close_path(); cr.fill()

    @staticmethod
    def _brush_outline(runs):
        if not runs: return ()
        rows = sorted(runs); left=[]; right=[]
        for a,b in zip(rows, rows[1:]):
            if b[0] != a[0]+1: return ()
        for dy, x0, _x1 in rows: left.extend(((x0,dy),(x0,dy+1)))
        for dy, _x0, x1 in reversed(rows): right.extend(((x1+1,dy+1),(x1+1,dy)))
        return tuple(left)+tuple(right)

    def _draw_brush_cursor(self, cr, z):
        if self._cursor is None or self._drawing or self.tool not in ("pencil","brush","eraser"):
            return
        runs = brush_runs(self.size, "round" if self.tool == "brush" else "square")
        points = self._brush_outline(runs)
        if not points: return
        cx, cy = self._cursor; cr.set_source_rgba(*_rgb("#1A1916"), .62); cr.set_line_width(1)
        cr.move_to((cx+points[0][0])*z+.5,(cy+points[0][1])*z+.5)
        for x,y in points[1:]: cr.line_to((cx+x)*z+.5,(cy+y)*z+.5)
        cr.close_path(); cr.stroke()

    def _set_tool(self, ident):
        if ident not in ("select","bubble","panel","picker"):
            self.previous_tool=ident
        self.tool = ident
        for key,button in self.tool_buttons.items():
            button.get_style_context().add_class("selected") if key==ident else button.get_style_context().remove_class("selected")
        self.bubble_group.set_sensitive(ident in ("select","bubble"))
        self._refresh()

    def _set_size(self,size):
        self.size=max(SIZE_MIN,min(SIZE_MAX,int(size))); self.size_lbl.set_text(str(self.size)); self.ramp_area.queue_draw(); self.canvas.queue_draw()

    def _draw_ramp(self,area,cr):
        w,h=area.get_allocated_width(),area.get_allocated_height(); step=w/float(len(SIZE_RAMP)); cr.set_source_rgb(*_rgb("#FCFBF8")); cr.paint()
        for i,n in enumerate(SIZE_RAMP):
            if n==self.size: cr.set_source_rgb(*_rgb("#EAE3D2")); cr.rectangle(i*step,0,step,h); cr.fill()
            d=max(3,min(step,h)*.62*(n/float(SIZE_RAMP[-1]))**.58); cr.set_source_rgb(*_rgb("#1A1916")); cr.rectangle(i*step+step/2-d/2,h/2-d/2,d,d); cr.fill()
        return False

    def _on_ramp_press(self,area,ev):
        index=max(0,min(len(SIZE_RAMP)-1,int(ev.x/(max(1,area.get_allocated_width())/len(SIZE_RAMP))))); self._set_size(SIZE_RAMP[index]); return True

    def _set_fill_shapes(self,filled):
        self.fill_shapes=bool(filled); self.outline_btn.set_active(not filled); self.filled_btn.set_active(filled)

    def _bubble_setting(self,key,value):
        # explicit, not setattr: the self_attr audit must be able to prove no
        # attribute of this class ever holds a caller-supplied callable
        if key=="style":self.bubble_style=value
        elif key=="size":self.bubble_size=value
        elif key=="bold":self.bubble_bold=value
        elif key=="italic":self.bubble_italic=value
        if self.selection and self.selection[0]=="bubble":
            before=self._snapshot(); bubble=self.doc.pages[self.doc.active]["bubbles"][self.selection[1]]; bubble[key]=value; grow_bubble(bubble); self._push(before,"Edit Bubble"); self._touch_page(self.doc.active)

    def _draw_colour_chip(self,area,cr):
        cr.set_source_rgb(*_rgb(self.color)); cr.rectangle(0,0,area.get_allocated_width(),area.get_allocated_height()); cr.fill(); return False

    def _draw_palette(self,_area,cr):
        for i,colour in enumerate(PALETTE):
            x=(i%PAL_COLS)*13; y=(i//PAL_COLS)*13; cr.set_source_rgb(*_rgb(colour)); cr.rectangle(x,y,12,12); cr.fill()
        return False

    def _on_palette_press(self,_area,ev):
        col,row=int(ev.x)//13,int(ev.y)//13; index=row*PAL_COLS+col
        if 0<=index<len(PALETTE):
            self.color=PALETTE[index]; self.colour_name.set_text(palette_name(index)); self.colour_chip.queue_draw(); self._recent=[self.color]+[x for x in self._recent if x!=self.color];
            self.recent_area.queue_draw()
            try: nbapp.atomic_write_json(PREFS_FILE,{"recent":self._recent[:16]})
            except Exception: pass
        return True

    def _draw_recent(self,_area,cr):
        for i,colour in enumerate(self._recent[:16]):cr.set_source_rgb(*_rgb(colour));cr.rectangle(i*13,0,12,12);cr.fill()
        return False

    def _on_recent_press(self,_area,ev):
        index=int(ev.x)//13
        if 0<=index<len(self._recent):self.color=self._recent[index];self.colour_name.set_text(mix_name(self.color));self.colour_chip.queue_draw()
        return True

    def _mix_prompt(self):
        state={"colour":self.color}; entry=Gtk.Entry(text=self.color); entry.connect("changed",lambda w:state.update(colour=w.get_text()))
        def apply():
            value=state["colour"].strip().upper()
            if len(value)==7 and value.startswith("#"):
                try:_rgb(value)
                except Exception:return
                self.color=value; self.colour_name.set_text(mix_name(value)); self.colour_chip.queue_draw(); self._recent=[value]+[x for x in self._recent if x!=value]
                try:nbapp.atomic_write_json(PREFS_FILE,{"recent":self._recent[:16]})
                except Exception:pass
        self._overlay_prompt("Mix Colour","Type a colour code, like #385C78.",[("Cancel",None),("Apply",apply)],entry)

    def _set_zoom(self, zoom):
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        self.canvas.set_size_request(int(PAGE_PX_W * self.zoom), int(PAGE_PX_H * self.zoom))
        lbl = getattr(self, "zoom_lbl", None)
        if lbl is not None:
            lbl.set_text("%d%%" % round(self.zoom * 100))
        self.canvas.queue_draw()

    def _step_zoom(self, delta):
        at = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        self._set_zoom(ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, at + delta))])

    def _toggle_grid(self):
        self.grid = not self.grid
        self.canvas.queue_draw()

    def _toggle_guides(self):
        self.page_guides = not self.page_guides
        self.canvas.queue_draw()

    def _toggle_bw(self):
        self.bw_inside = not self.bw_inside
        self.canvas.queue_draw()

    def _toggle_mask(self):
        before = self._snapshot()
        page = self.doc.pages[self.doc.active]
        page["mask_gutters"] = not page["mask_gutters"]
        self._push(before, "Hide Art Outside Panels")

    def _structure(self, fn, name):
        before = self._snapshot()
        if not fn():
            return False
        self._push(before, name)
        self._refresh()
        return True

    def _add_page(self): return self._structure(lambda: self.doc.add_page(), "New Page")
    def _duplicate_page(self): return self._structure(lambda: self.doc.add_page(duplicate=True), "Duplicate Page")
    def _delete_page(self): return self._structure(self.doc.delete_page, "Delete Page")
    def _move_page(self, d): return self._structure(lambda: self.doc.move_page(d), "Move Page")

    def _new_layer(self):
        page = self.doc.pages[self.doc.active]
        if len(page["layers"]) >= LAYER_MAX: return False
        before=self._snapshot(); page["layers"].insert(self.active_layer+1,Layer("Layer %d"%(len(page["layers"])+1),surface=_surface(False))); self.active_layer+=1; self._push(before,"New Layer"); self._touch_page(self.doc.active,objects=False); self._refresh(); return True

    def _delete_layer(self):
        page = self.doc.pages[self.doc.active]
        if len(page["layers"])<=1:return False
        before=self._snapshot(); page["layers"].pop(self.active_layer); self.active_layer=max(0,min(self.active_layer,len(page["layers"])-1)); self._push(before,"Delete Layer"); self._touch_page(self.doc.active,objects=False); self._refresh(); return True

    def _clear_layer(self):
        page = self.doc.pages[self.doc.active]
        before = self._snapshot()
        page["layers"][self.active_layer].surface = _surface(False)
        page["layers"][self.active_layer].touch()
        self._push(before, "Clear Layer")

    def _delete_selection(self):
        if not self.selection:
            return False
        kind, index = self.selection
        seq = self.doc.pages[self.doc.active]["bubbles" if kind == "bubble" else "panels"]
        if not 0 <= index < len(seq):
            return False
        before = self._snapshot()
        seq.pop(index)
        self.selection = None
        self._push(before, "Delete Bubble" if kind == "bubble" else "Delete Panel")
        return True

    def _bubble_forward(self): return self._arrange_bubble(1, "Bring Bubble Forward")
    def _bubble_backward(self): return self._arrange_bubble(-1, "Send Bubble Backward")

    def _arrange_bubble(self, delta, name):
        if not self.selection or self.selection[0] != "bubble":
            return False
        bubbles, at = self.doc.pages[self.doc.active]["bubbles"], self.selection[1]
        to = at + delta
        if not 0 <= to < len(bubbles):
            return False
        before = self._snapshot()
        bubbles[at], bubbles[to] = bubbles[to], bubbles[at]
        self.selection = ("bubble", to)
        self._push(before, name)
        return True

    def _panel_layout_prompt(self):
        state = {"preset": 3, "margin": "90", "gutter": "42"}
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        combo = Gtk.ComboBoxText()
        for label in ("1", "2 rows", "3 rows", "2x2", "2x3", "3x3"):
            combo.append_text(_t(label))
        combo.set_active(3)
        combo.connect("changed", lambda w: state.update(preset=w.get_active()))
        content.pack_start(combo, False, False, 0)
        entries = []
        for label, key in (("Margin", "margin"), ("Gutter", "gutter")):
            row = Gtk.Box(spacing=8)
            row.pack_start(Gtk.Label(label=_t(label)), False, False, 0)
            entry = Gtk.Entry(text=state[key])
            entry.connect("changed", lambda w, key=key: state.update({key: w.get_text()}))
            row.pack_start(entry, True, True, 0)
            content.pack_start(row, False, False, 0)
            entries.append(entry)

        def apply():
            try:
                made = panel_layout(state["preset"], int(state["margin"]), int(state["gutter"]))
            except (ValueError, TypeError):
                self._flash(_t("Panel layout does not fit the page."), False)
                return
            before = self._snapshot()
            self.doc.pages[self.doc.active]["panels"] = made
            self._push(before, "Panel Layout")
        self._overlay_prompt("Panel Layout", "Choose a panel grid.",
                             [("Cancel", None), ("Apply", apply)], content)
        self._dialog_widgets = {"preset": combo, "margin": entries[0], "gutter": entries[1]}

    def _bubble_editor(self, index, new_before=None):
        bubble = self.doc.pages[self.doc.active]["bubbles"][index]
        before = self._snapshot()
        original = copy.deepcopy(bubble)
        state = copy.deepcopy(bubble)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        view = Gtk.TextView()
        view.set_size_request(360, 96)
        view.get_buffer().set_text(state["text"])

        def changed(buf):
            start, end = buf.get_bounds()
            state["text"] = buf.get_text(start, end, True)
            bubble.update(state)
            grow_bubble(bubble)
            self._object_overlay.pop(self.doc.active, None)
            self.canvas.queue_draw()
        view.get_buffer().connect("changed", changed)
        content.pack_start(view, True, True, 0)

        def cancel():
            if new_before is not None:
                self._restore_snapshot(new_before)
                self.selection = None
            else:
                bubble.clear(); bubble.update(original)
                self._object_overlay.pop(self.doc.active, None)
            self.canvas.queue_draw()

        def apply():
            bubble.clear(); bubble.update(state); grow_bubble(bubble)
            self._push(new_before or before,
                       "Add Bubble" if new_before is not None else "Edit Bubble")
            self._touch_page(self.doc.active)
        self._overlay_prompt("Edit Bubble", "Edit the bubble lettering.",
                             [("Cancel", cancel), ("Apply", apply)], content,
                             cancel=cancel)
        self._dialog_widgets = {"text": view}

    def _overlay_prompt(self, title, body, buttons, content=None, cancel=None):
        """Modal in-window prompt, Illustrator's idiom: a Fixed layer holding
        a scrim sized to the LIVE window and a card moved to its measured
        centre. The inner-Overlay version this replaces let the scrim's size
        request steer the whole layer, which parked the card off the window's
        bottom-right corner."""
        self._close_prompt(run_cancel=False)
        # Centre in the HOST overlay's coordinate space — the layer lives
        # there, so its allocation is the truth even when the toplevel's own
        # allocation is not live (the offscreen render harness).
        alloc = self._prompt_host.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        layer = Gtk.Fixed()
        layer._cancel = cancel
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.get_style_context().add_class("comics-scrim")
        scrim.connect("button-press-event", lambda *_: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("comics-prompt")
        card.pack_start(Gtk.Label(label=_t(title), xalign=0), False, False, 0)
        bd = Gtk.Label(label=_t(body), xalign=0)
        bd.set_line_wrap(True)
        bd.set_max_width_chars(38)
        card.pack_start(bd, False, False, 0)
        if content:
            card.pack_start(content, False, False, 0)
        row = Gtk.Box(spacing=8)
        row.set_halign(Gtk.Align.END)
        focus_btn = None
        for label, callback in buttons:
            btn = Gtk.Button(label=_t(label))
            btn.connect("clicked", lambda _w, cb=callback: (self._close_prompt(run_cancel=False), cb and cb())[1])
            row.pack_start(btn, False, False, 0)
            # keyboard focus rests on the safe first button (Cancel), so a
            # stray Return can never fire the committing action by default
            if focus_btn is None:
                focus_btn = btn
        card.pack_start(row, False, False, 0)
        card_win = Gtk.EventBox()
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._prompt_host.add_overlay(layer)
        self._prompt_layer = layer
        layer.show_all()
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 420
        ch = nat.height if nat.height > 1 else 200
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        if focus_btn is not None:
            focus_btn.grab_focus()

    def _close_prompt(self, run_cancel=True):
        layer, self._prompt_layer = self._prompt_layer, None
        if layer:
            cancel = getattr(layer, "_cancel", None)
            self._prompt_host.remove(layer)
            if run_cancel and cancel:
                cancel()

    def _open_path(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            doc, reports = ComicDocument.parse(data)
            if doc is None:
                raise ValueError("document shape")
        except Exception:
            return False
        self.doc, self.doc_path = doc, path
        self.doc.doc_path = path
        return not reports

    def _new(self):
        before = self._snapshot()
        self.doc, self.doc_path = ComicDocument(), None
        self._push(before, "New Document")
        self._refresh()

    def _open(self):
        path = nbpicker.open_file(self, title="Open Comic", start_dir=DOCS_DIR, patterns=("*.comic",))
        if path:
            before = self._snapshot()
            if self._open_path(path):
                self._push(before, "Open Document")
                self._refresh()

    def _save(self):
        if not self.doc_path:
            return self._save_as()
        return self._write_document(self.doc_path)

    def _save_as(self):
        path = nbpicker.save_file(self, title="Save Comic As", start_dir=DOCS_DIR,
                                  suggested_name=os.path.basename(self.doc_path) if self.doc_path else "comic.comic",
                                  patterns=("*.comic",), default_ext=".comic")
        if path and self._write_document(path):
            self.doc_path = path
            self.doc.doc_path = path
            self._refresh()
            return True
        return False

    def _write_document(self, path):
        try:
            save_document(self.doc, path)
        except Exception as exc:
            self._save_error = exc
            self._flash(_t(nbapp.save_failure_reason(exc, path)), False)
            return False
        self._flash(_t("Saved %s") % time.strftime("%H:%M"), True)
        return True

    def _copy_page(self):
        try:
            raw = _png(flatten_page(self.doc.pages[self.doc.active]))
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(raw); loader.close()
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_image(loader.get_pixbuf()); clip.store()
        except Exception:
            pass

    def _place_image(self):
        path = nbpicker.open_file(self, title="Place Image", start_dir=PICS_DIR, patterns=("*.png",))
        if not path:
            return
        try:
            image = cairo.ImageSurface.create_from_png(path)
        except Exception:
            self._flash(_t("The image could not be placed."), False)
            return
        before = self._snapshot()
        target = self.doc.pages[self.doc.active]["layers"][self.active_layer].decode()
        scale = _place_scale(image.get_width(), image.get_height())
        cr = cairo.Context(target); cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.translate((PAGE_PX_W - image.get_width() * scale) // 2,
                     (PAGE_PX_H - image.get_height() * scale) // 2)
        cr.scale(scale, scale)
        pat = cairo.SurfacePattern(image); pat.set_filter(cairo.FILTER_NEAREST)
        cr.set_source(pat); cr.paint()
        self.doc.pages[self.doc.active]["layers"][self.active_layer].touch()
        self._push(before, "Place Image")

    def _export_prompt(self):
        state = {"name": os.path.splitext(os.path.basename(self.doc_path or "comic"))[0], "bw": False}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(text=state["name"])
        entry.connect("changed", lambda w: state.update(name=w.get_text()))
        check = Gtk.CheckButton(label=_t("Inside pages in black and white"))
        check.connect("toggled", lambda w: state.update(bw=w.get_active()))
        box.pack_start(entry, False, False, 0); box.pack_start(check, False, False, 0)
        self._overlay_prompt("Export to PDF", "Export sequential comic pages.",
                             [("Cancel", None), ("Export", lambda: self._export(state))], box)

    def _export(self, state):
        name = os.path.basename(state["name"].strip() or "comic") + ".pdf"
        path = os.path.join(DOCS_DIR, name)
        if os.path.exists(path) and not state.get("replace"):
            next_state=dict(state); next_state["replace"]=True
            self._overlay_prompt("Replace file?", _t("\u201c%s\u201d already exists in Documents. Replace it?") % name,
                                 [("Cancel",None),("Replace",lambda:self._export(next_state))])
            return
        cache = collections.OrderedDict()
        covers = {0, 1, len(self.doc.pages) - 2, len(self.doc.pages) - 1}
        def draw(cr, number, _w, _h):
            idx = number - 1
            flat = _cached_flatten(cache, self.doc.pages, idx)
            if state["bw"] and idx not in covers:
                flat = desaturate(flat)
            draw_flat_page(cr, flat)
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            nbprint.simple_pdf(path, len(self.doc.pages), draw,
                               nbprint.HALF_W_PT, nbprint.HALF_H_PT)
            self._flash(_t("Exported %s") % time.strftime("%H:%M"), True)
        except Exception:
            self._flash(_t("The PDF could not be exported."), False)

    def _print_prompt(self):
        state = {"sheets": 0, "bw": False}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        first=None
        for i,item in enumerate(("Everything","Cover sheet","Inside sheets")):
            radio=Gtk.RadioButton.new_with_label_from_widget(first,_t(item)); first=first or radio; radio.set_active(i==0); radio.connect("toggled",lambda w,i=i:w.get_active() and state.update(sheets=i))
            if i==2 and len(self.doc.pages)<=4:radio.set_sensitive(False); radio.set_tooltip_text(_t("A four-page comic has no inside sheets."))
            box.pack_start(radio,False,False,0)
        check = Gtk.CheckButton(label=_t("Inside pages in black and white"))
        check.connect("toggled", lambda w: state.update(bw=w.get_active()))
        box.pack_start(check, False, False, 0)
        box.pack_start(Gtk.Label(label=_t("The cover sheet and its inside faces print in colour."), xalign=0), False, False, 0)
        total=len(self.doc.pages); blanks=(-total)%4; mathline=_t("Letter sheets: %d")%((total+3)//4)
        if blanks==1:mathline+="\n"+_t("One page prints blank before the back cover.")
        elif blanks:mathline+="\n"+(_t("%d pages print blank before the back cover.")%blanks)
        box.pack_start(Gtk.Label(label=mathline,xalign=0),False,False,0)
        self._overlay_prompt("Zine Print", "Choose the sheets to print.",
                             [("Cancel", None), ("Print", lambda: self._print(state))], box)

    def _print(self, state):
        order = _page_order(len(self.doc.pages))
        cache, colour = collections.OrderedDict(), cover_pages(order)
        def prepare(path):
            def draw(cr, page_no, _w, _h):
                idx = page_no - 1
                flat = _cached_flatten(cache, self.doc.pages, idx)
                if state["bw"] and page_no not in colour:
                    flat = desaturate(flat)
                draw_flat_page(cr, flat)
            _impose(path, order, ("all", "cover", "inside")[state["sheets"]], True, draw)
        nbprint.print_booklet(self, prepare, "Comics")

    def _flash(self, text, saved=False):
        self._render_chip("saved" if saved else "error", text)

    def _render_chip(self, state, text=None):
        if state == "saved":
            colour = "#7FA98C"
            wording = text or (_t("Saved %s") % time.strftime("%H:%M"))
        else:
            colour = "#C8341E"
            wording = text or _t("Unsaved changes")
        self.save_lbl.set_markup('<span foreground="%s">\u25cf </span>%s' %
                                 (colour, GLib.markup_escape_text(wording)))

    def _on_key(self, _w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        if ev.keyval == Gdk.KEY_Escape and self._prompt_layer:
            self._close_prompt()
            return True
        if ev.keyval == Gdk.KEY_Escape and self.selection:
            self.selection=None; self.canvas.queue_draw(); return True
        if ev.keyval == Gdk.KEY_Delete:
            return bool(self._delete_selection())
        if ev.keyval in (Gdk.KEY_Left,Gdk.KEY_Right,Gdk.KEY_Up,Gdk.KEY_Down) and self.selection:
            dx=(-1 if ev.keyval==Gdk.KEY_Left else 1 if ev.keyval==Gdk.KEY_Right else 0)*(30 if shift else 1); dy=(-1 if ev.keyval==Gdk.KEY_Up else 1 if ev.keyval==Gdk.KEY_Down else 0)*(30 if shift else 1); self._nudge(dx,dy); return True
        if ev.keyval == Gdk.KEY_Page_Down:
            return self._switch_page(min(len(self.doc.pages) - 1, self.doc.active + 1))
        if ev.keyval == Gdk.KEY_Page_Up:
            return self._switch_page(max(0, self.doc.active - 1))
        if ctrl and ev.keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self._save_as() if shift else self._save(); return True
        if nbapp.undo_keys(self.history,ev): return True
        if ctrl and ev.keyval in (Gdk.KEY_n,Gdk.KEY_N): self._new(); return True
        if ctrl and ev.keyval in (Gdk.KEY_o,Gdk.KEY_O): self._open(); return True
        if ctrl and ev.keyval in (Gdk.KEY_plus,Gdk.KEY_KP_Add): self._step_zoom(1); return True
        if ctrl and ev.keyval in (Gdk.KEY_minus,Gdk.KEY_KP_Subtract): self._step_zoom(-1); return True
        if ctrl and ev.keyval in (Gdk.KEY_0,Gdk.KEY_KP_0): self._set_zoom(1); return True
        if ctrl and ev.keyval in (Gdk.KEY_9,Gdk.KEY_KP_9): self._zoom_fit(); return True
        if not ctrl and ev.keyval==Gdk.KEY_bracketleft: self._set_size(self.size-1); return True
        if not ctrl and ev.keyval==Gdk.KEY_bracketright: self._set_size(self.size+1); return True
        if not ctrl and ev.keyval in (Gdk.KEY_plus,Gdk.KEY_KP_Add): self._step_zoom(1); return True
        if not ctrl and ev.keyval in (Gdk.KEY_minus,Gdk.KEY_KP_Subtract): self._step_zoom(-1); return True
        if not ctrl and ev.keyval in (Gdk.KEY_0,Gdk.KEY_KP_0): self._set_zoom(1); return True
        keys = {Gdk.KEY_v: "select", Gdk.KEY_p: "pencil", Gdk.KEY_b: "brush",
                Gdk.KEY_e: "eraser", Gdk.KEY_f: "fill", Gdk.KEY_l: "line",
                Gdk.KEY_r: "rect", Gdk.KEY_o: "ellipse", Gdk.KEY_i: "picker",
                Gdk.KEY_w: "bubble", Gdk.KEY_n: "panel"}
        if not ctrl and ev.keyval in keys:
            self._set_tool(keys[ev.keyval]); return True
        return False

    def _nudge(self,dx,dy):
        if not self.selection:return False
        if self._nudge_before is None:self._nudge_before=self._snapshot()
        kind,index=self.selection; obj=self.doc.pages[self.doc.active]["bubbles" if kind=="bubble" else "panels"][index]
        nx=max(0,min(PAGE_PX_W-obj["w"],obj["x"]+dx)); ny=max(0,min(PAGE_PX_H-obj["h"],obj["y"]+dy)); ax,ay=nx-obj["x"],ny-obj["y"]; obj["x"],obj["y"]=nx,ny
        if obj.get("tail") is not None:obj["tail"]=[obj["tail"][0]+ax,obj["tail"][1]+ay]
        self._object_overlay.pop(self.doc.active,None); self._cancel_source("nudge"); self._src["nudge"]=GLib.timeout_add(400,self._finish_nudge); self.canvas.queue_draw(); return True

    def _finish_nudge(self):
        self._src.pop("nudge", None)
        if self._closed:return False
        if self._nudge_before is not None:self._push(self._nudge_before,"Nudge Bubble" if self.selection and self.selection[0]=="bubble" else "Nudge Panel"); self._nudge_before=None; self._touch_page(self.doc.active)
        return False

    def _cancel_source(self, key):
        sid = self._src.pop(key, 0)
        if sid:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass

    def _on_delete(self, *_args):
        if not self._recovery_dirty:
            return False
        if self._save_state_sync():
            return False
        save_label = "Save As\u2026" if self._store_read_only else "Save"
        save_cb = self._save_as if self._store_read_only else self._autosave
        self._overlay_prompt("Not saved", nbapp.save_failure_reason(self._save_error, COMICS_FILE),
                             [("Cancel", None), ("Discard", self._discard_and_close),
                              (save_label, save_cb)])
        return True

    def _discard_and_close(self):
        self._discarded = True
        self.destroy()

    def _on_destroy(self, *_args):
        if self._closed:
            return False
        if self._recovery_dirty and not self._discarded and not self._store_read_only:
            self._save_state_sync()
        self._closed = True
        for key in list(self._src):
            self._cancel_source(key)
        self.jobs.close()
        return False


def main():
    app = Comics(sys.argv[1] if len(sys.argv) > 1 else None)
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
