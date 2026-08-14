"""Animation - Notebook OS's exposure-sheet 2D animation studio.

The model and raster helpers are deliberately usable without a display.  Gst
and ffmpeg are discovered only when playback/export is requested.
"""
import array, base64, collections, copy, io, json, math, os, random
import re
import shutil, struct, subprocess, sys, tempfile, threading, time, wave, zlib
import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango, PangoCairo
import nbapp
import nbicons
import nbpicker
import nbi18n
from nbi18n import _t
SELFTEST_MARKER = 'animation-062-real'
FORMAT = 1
SPF = {6: 8000, 8: 6000, 10: 4800, 12: 4000, 15: 3200, 24: 2000}
FPS_VALUES = tuple(SPF)
CANVAS_PRESETS = ((160, 120), (240, 240), (320, 180), (320, 240), (480, 270), (640, 360), (640, 480))
CONFORM_FPS = {6: 24, 8: 24, 10: 30, 12: 24, 15: 30, 24: 24}
CEL_MAX, SCENE_MAX, LAYER_MAX = (768, 64, 6)
SCENE_FRAME_MAX, PROJECT_FRAME_MAX = (4800, 43200)
TAKE_MAX, SOUND_ROWS = (5, 2)
UNDO_DEPTH, HISTORY_BYTES = (200, 96 * 1024 * 1024)
ZOOM_STEPS = (1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32)

# Timeline geometry, in one place. A gutter names every row, the ruler
# numbers the frames, the strip carries the scene cards and the transport.
# Frame 0 begins at TL_GUTTER on every band, and every press/motion handler
# shares these numbers with the draw code.
TL_GUTTER = 92
TL_STRIP_H = 36
TL_RULER_H = 26
TL_ROW_H = 22
TL_ROWS_TOP = TL_STRIP_H + TL_RULER_H
# Thumbnails frame the ink, not the sheet. A mouth drawn 15x1 in the middle
# of a 320x240 cel is a tenth of a pixel tall scaled whole-canvas, and the
# mouths are precisely the drawings someone has to tell apart to fill the
# slots. The margin gives the shape air; the zoom cap keeps a speck a speck.
THUMB_W = 44
THUMB_H = 33
THUMB_PAD = .15
THUMB_ZOOM_MAX = 6.
PATTERNS = ('solid', 'checker', 'sparse')
_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
NB_HOME = os.environ.get('NB_HOME', os.path.expanduser('~'))
DOCS_DIR = os.path.join(NB_HOME, 'Documents')
MUSIC_DIR = os.path.join(NB_HOME, 'Music')
VIDEOS_DIR = os.path.join(NB_HOME, 'Videos')
PICTURES_DIR = os.path.join(NB_HOME, 'Pictures')
STORE_FILE = os.path.join(NB_HOME, '.config', 'notebook', 'animation.json')
TOOLS = (('select', 'Select', 'V'), ('pencil', 'Pencil', 'P'), ('brush', 'Brush', 'B'), ('eraser', 'Eraser', 'E'), ('fill', 'Fill', 'F'), ('line', 'Line', 'L'), ('rect', 'Rectangle', 'R'), ('ellipse', 'Ellipse', 'O'), ('picker', 'Eyedropper', 'I'))
TOOL_HINTS = {
    'select': 'Click artwork to select its exposure. Drag to move it.',
    'pencil': 'Drag to draw. Square tip, hard edges.',
    'brush': 'Drag to draw. Round tip, hard edges.',
    'eraser': 'Drag to erase to transparent.',
    'fill': 'Click an area to flood it with the colour.',
    'line': 'Drag end to end. Hold Shift for 45° steps.',
    'rect': 'Drag corner to corner. Hold Shift for a square.',
    'ellipse': 'Drag corner to corner. Hold Shift for a circle.',
    'picker': 'Click the artwork to take that colour.',
}
_HUES = (('Red', 0), ('Coral', 14), ('Orange', 30), ('Amber', 44), ('Yellow', 56), ('Lime', 82), ('Green', 122), ('Emerald', 150), ('Teal', 172), ('Cyan', 188), ('Azure', 205), ('Blue', 222), ('Indigo', 244), ('Purple', 276), ('Magenta', 305), ('Pink', 332))
_VALUES = (('Darkest %s', 0.7, 0.28, 250, 10), ('Dark %s', 0.85, 0.48, 250, 5), (None, 0.9, 0.72, None, 0), ('Bright %s', 0.85, 0.95, 50, 5), ('Pale %s', 0.35, 1.0, 50, 4))
_MUTED = ('Muted %s', 0.3, 0.62)
_NEUTRALS = (('Black', 0), ('Ink', 36), ('Slate', 72), ('Grey', 112), ('Silver', 153), ('Ash', 187), ('Paper', 221), ('White', 255))
_STAPLES = (('Brown', '#6B4A2F'), ('Tan', '#C9A26A'), ('Cream', '#F2E4C4'), ('Olive', '#6E7B3F'), ('Navy', '#20325C'), ('Maroon', '#5E1F28'), ('Gold', '#D9A21B'), ('Peach', '#F2B79A'))

def _hsv_hex(h, s, v):
    h %= 360.0
    c = v * s
    x = c * (1 - abs(h / 60.0 % 2 - 1))
    m = v - c
    r, g, b = ((c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x))[int(h // 60) % 6]
    return '#%02X%02X%02X' % (round((r + m) * 255), round((g + m) * 255), round((b + m) * 255))

def _toward(h, t, a):
    d = (t - h + 180) % 360 - 180
    return float(t) if abs(d) <= a else (h + (a if d > 0 else -a)) % 360

def _build_palette():
    out = []
    for _, s, v, t, a in _VALUES:
        out += [_hsv_hex(_toward(h, t, a) if t is not None else h, s, v) for _, h in _HUES]
    out += [_hsv_hex(h, _MUTED[1], _MUTED[2]) for _, h in _HUES]
    out += ['#%02X%02X%02X' % (g, g, g) for _, g in _NEUTRALS]
    out += [c for _, c in _STAPLES]
    return out
PALETTE = _build_palette()

# One ASCII-only application sheet.  Every colour is a design token named by
# ANIMATION-SPEC section 12; square corners are part of the paper vocabulary.
CSS = b"""
.animation { background: #FCFBF8; color: #1A1916; }
.animation * { border-radius: 0; }
.animation-dock, .animation-side { background: #FCFBF8; }
.animation-mat { background: #DED4C2; }
.animation-group { color: #6E695E; }
.animation-muted { color: #9A9484; }
.animation-row { border-bottom: 1px solid #C9C4B6; }
.animation-row:checked, .animation-selected { background: #EAE3D2; }
.animation-slot-tile { padding: 2px; }
.animation-slot-badge { background: #FCFBF8; color: #1A1916; font-size: 11px; padding: 0 3px; border: 1px solid #C9C4B6; }
.animation-take-option { background: transparent; border: 1px solid transparent; padding: 2px 4px; }
.animation-take-option:checked { background: #EFEBE0; border: 1px solid #C8341E; }
.animation-take-option label { color: inherit; }
.animation-prompt { background: #FCFBF8; border: 1px solid #C9C4B6; padding: 24px; }
.animation-focus { border: 1px solid #C8341E; }
.animation-saved { color: #7FA98C; }
.animation-unsaved { color: #C8341E; }
.animation-stepbtn { min-width: 30px; min-height: 30px; padding: 0;
                     background: #FCFBF8; border: 1px solid #C9C4B6; }
.animation-stepbtn:hover { background: #EAE3D2; }
.animation-stepbtn:checked { background: #EAE3D2; border-color: #9A9484; }
.animation-stepbtn.animation-wide { padding: 0 6px; }
.animation-marklabel { font-size: 12px; color: #1A1916; }
"""

def _pango_layout(cr, text, size, bold=False):
    """A Pango layout in the interface face (academics.py's helper — cairo's
    toy text API draws .notdef boxes, or nothing at all, for every non-Latin
    script the OS ships)."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription('Nimbus Sans')
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    layout.set_text(text, -1)
    return layout


def _show_text(cr, x, y, text, size=12, bold=False):
    """Draw text with its BASELINE at y, the anchor cr.show_text used, so the
    timeline keeps the geometry its rows were tuned with."""
    layout = _pango_layout(cr, text, size, bold)
    cr.move_to(x, y - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)


def _rgb255(h):
    h = h.lstrip('#')
    return tuple((int(h[i:i + 2], 16) for i in (0, 2, 4)))

def px4(h):
    """Return an opaque cairo ARGB32 pixel in native little-endian order.

    Copied behaviour-for-behaviour from illustrator.py, the source of truth
    for Notebook OS's byte-exact pixel engine.
    """
    r, g, b = _rgb255(h)
    return bytes((b, g, r, 255))
CLEAR4 = b'\x00\x00\x00\x00'
_RUNS = {}

def brush_runs(size, shape):
    """Build Illustrator's square or hard round brush as horizontal runs."""
    key = (int(size), shape)
    if key in _RUNS:
        return _RUNS[key]
    n = max(1, min(192, int(size)))
    o = n // 2
    out = []
    if shape == 'round' and n > 2:
        c = (n - 1) / 2.0
        rr = (n / 2.0 - 0.15) ** 2
        for j in range(n):
            row = [i for i in range(n) if (i - c) ** 2 + (j - c) ** 2 <= rr]
            if row:
                out.append((j - o, row[0] - o, row[-1] - o))
    else:
        out = [(j - o, -o, n - 1 - o) for j in range(n)]
    _RUNS[key] = tuple(out)
    return _RUNS[key]

def _line_points(x0, y0, x1, y1):
    """Illustrator's inclusive integer Bresenham line."""
    x0, y0, x1, y1 = map(int, (x0, y0, x1, y1))
    dx, dy = (abs(x1 - x0), abs(y1 - y0))
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return out
        e = 2 * err
        if e > -dy:
            err -= dy
            x0 += sx
        if e < dx:
            err += dx
            y0 += sy

def _ellipse_spans(x0, y0, x1, y1):
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    cx, cy = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    rx, ry = ((x1 - x0 + 1) / 2.0, (y1 - y0 + 1) / 2.0)
    out = []
    for y in range(y0, y1 + 1):
        v = 1 - ((y - cy) / ry) ** 2
        if v < 0:
            continue
        d = math.sqrt(v) * rx
        a = int(math.ceil(cx - d - 0.5))
        b = int(math.floor(cx + d + 0.5)) - 1
        if b < a:
            a = b = int(round(cx))
        out.append((y, max(x0, a), min(x1, b)))
    return out

def _ellipse_outline(spans):
    out = []
    prev = None
    for i, (y, a, b) in enumerate(spans):
        if prev is None or i == len(spans) - 1:
            out += [(x, y) for x in range(a, b + 1)]
        else:
            pa, pb = prev
            out += [(x, y) for x in range(min(a, pa), max(a, pa) + 1)]
            out += [(x, y) for x in range(min(b, pb), max(b, pb) + 1)]
        prev = (a, b)
    return out

def _snap45(a, b):
    dx, dy = (b[0] - a[0], b[1] - a[1])
    if not dx and (not dy):
        return b
    ax, ay = (abs(dx), abs(dy))
    if ax > 2 * ay:
        return (b[0], a[1])
    if ay > 2 * ax:
        return (a[0], b[1])
    n = max(ax, ay)
    return (a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n))

def _square(a, b):
    dx, dy = (b[0] - a[0], b[1] - a[1])
    n = max(abs(dx), abs(dy))
    return (a[0] + (n if dx >= 0 else -n), a[1] + (n if dy >= 0 else -n))

def pattern_allows(pattern, x, y):
    return pattern == 'solid' or (pattern == 'checker' and (not x + y & 1)) or (pattern == 'sparse' and (not x & 1) and (not y & 1))

def surface(w, h, white=False):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    if white:
        c = cairo.Context(s)
        c.set_source_rgb(1, 1, 1)
        c.paint()
    return s

def write_pixel(s, x, y, value, pattern='solid', symx=False, symy=False):
    w, h = (s.get_width(), s.get_height())
    value = px4(value) if isinstance(value, str) else value
    pts = {(int(x), int(y))}
    if symx:
        pts |= {(w - 1 - a, b) for a, b in tuple(pts)}
    if symy:
        pts |= {(a, h - 1 - b) for a, b in tuple(pts)}
    s.flush()
    d = s.get_data()
    stride = s.get_stride()
    for a, b in pts:
        if 0 <= a < w and 0 <= b < h and pattern_allows(pattern, a, b):
            d[b * stride + a * 4:b * stride + a * 4 + 4] = value
    s.mark_dirty()

def write_span(s, y, x0, x1, value, pattern='solid', symx=False, symy=False):
    for x in range(int(x0), int(x1) + 1):
        write_pixel(s, x, y, value, pattern, symx, symy)


def pix_at(image, x, y):
    """Read one native ARGB32 pixel, returning transparent outside bounds."""
    if not 0 <= x < image.get_width() or not 0 <= y < image.get_height():
        return CLEAR4
    image.flush()
    offset = int(y) * image.get_stride() + int(x) * 4
    return bytes(image.get_data()[offset:offset + 4])

def stamp(s, x, y, size, shape, value, pattern='solid', symx=False, symy=False):
    for dy, a, b in brush_runs(size, shape):
        write_span(s, y + dy, x + a, x + b, value, pattern, symx, symy)

def surface_png(s):
    out = io.BytesIO()
    s.write_to_png(out)
    return out.getvalue()

def png_surface(raw, w=None, h=None):
    try:
        return cairo.ImageSurface.create_from_png(io.BytesIO(raw))
    except Exception:
        return surface(w or 1, h or 1)

def png_b64(s):
    return base64.b64encode(surface_png(s)).decode('ascii')

def decode_b64(s, w, h):
    return png_surface(base64.b64decode(s), w, h)

PNG_SIG = b'\x89PNG\r\n\x1a\n'

def png_intact(blob, w, h):
    """Is this PNG whole, without unpacking a single pixel?

    Opening a film decodes every take to find damage and throws the picture
    away — 3840 decodes at the cap, 2.6 seconds of frozen window on this
    machine and far worse on the one the OS is for. Walking the file's own
    structure answers the same question: the signature, every chunk's CRC
    (which covers the compressed pixels), the geometry IHDR declares, and a
    terminating IEND. That is 115x cheaper than inflating the image.

    This may only say *yes*. A no sends the take to the real decoder, which
    stays the only thing allowed to call a drawing damaged — because a take
    wrongly called damaged is replaced with blank paper, and a false alarm
    here would destroy work rather than report it.
    """
    if not blob.startswith(PNG_SIG):
        return False
    at, seen_header, ended = len(PNG_SIG), False, False
    while at + 8 <= len(blob):
        length = int.from_bytes(blob[at:at + 4], 'big')
        kind = blob[at + 4:at + 8]
        stop = at + 8 + length + 4
        if stop > len(blob):
            return False
        body = blob[at + 8:at + 8 + length]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != int.from_bytes(blob[stop - 4:stop], 'big'):
            return False
        if kind == b'IHDR':
            if length != 13:
                return False
            width = int.from_bytes(body[0:4], 'big')
            height = int.from_bytes(body[4:8], 'big')
            if (width, height) != (w, h) or body[8] != 8 or body[9] not in (2, 6):
                return False
            seen_header = True
        elif kind == b'IEND':
            ended = True
        at = stop
    return seen_header and ended and at == len(blob)

def normalize_runs(runs, length):
    out = []
    for r in sorted((copy.deepcopy(x) for x in runs), key=lambda x: x['start']):
        r['start'] = max(0, int(r['start']))
        r['len'] = min(int(r['len']), length - r['start'])
        if r['len'] <= 0:
            continue
        if out and r['start'] < out[-1]['start'] + out[-1]['len']:
            raise ValueError('overlapping exposures')
        out.append(r)
    return out

def run_at(runs, frame):
    return next((r for r in runs if r['start'] <= frame < r['start'] + r['len']), None)

def take_index(run, frame, ntakes, boil_every):
    if run.get('take', 0) > 0:
        return min(ntakes - 1, run['take'] - 1)
    return (frame - run['start']) // boil_every % ntakes

def make_run(cel, start, length, dx=0, dy=0, take=0):
    return {'cel': int(cel), 'start': int(start), 'len': int(length), 'dx': int(dx), 'dy': int(dy), 'take': int(take)}

class Cel:

    def __init__(self, id_, name, takes, w, h, extra=None):
        self.id = id_
        self.name = name
        self.takes = takes
        self.w = w
        self.h = h
        self.version = 0
        self._extra = extra or {}

    def decoded(self, i=0):
        """The take as a surface, never an exception.

        A load already replaces takes it cannot read (parse strict-decodes),
        but this is the path every DRAW goes through — and a decode that
        raises here takes the window down mid-paint, which is the worst
        place in the app to fail. Anything unreadable becomes blank paper
        and the drawing survives as an empty cel.
        """
        t = self.takes[i]
        if isinstance(t, cairo.ImageSurface):
            return t
        try:
            s = (decode_b64(t, self.w, self.h) if isinstance(t, str)
                 else png_surface(t, self.w, self.h))
        except Exception:
            s = surface(self.w, self.h)
        self.takes[i] = s
        return s

    def serial(self):
        """The cel as plain data, re-encoding only what changed.

        Every snapshot serialises the whole film, and a snapshot is taken
        for each brush stroke — so PNG-encoding every take of every drawing
        every time put a stroke at 92ms on a twenty-drawing film and 812ms on a
        hundred and fifty, against a fifty-millisecond budget.

        The cache is keyed on the version AND on the identity of the take
        objects, so replacing a take invalidates it even without a bump.
        The remaining way to serve stale bytes is to paint into an existing
        take and not raise the version; F33 checks exactly that, on every
        path that draws, by comparing this against a fresh encoding.
        """
        stamp = (self.version, tuple(id(take) for take in self.takes))
        if getattr(self, '_serial_stamp', None) != stamp:
            self._serial_takes = [
                png_b64(x) if isinstance(x, cairo.ImageSurface)
                else base64.b64encode(x).decode('ascii') if isinstance(x, bytes)
                else x for x in self.takes]
            self._serial_stamp = stamp
        return dict(self._extra, id=self.id, name=self.name,
                    takes=list(self._serial_takes))

    def encoded_afresh(self):
        """The same data with no cache in the way — the check's reference."""
        return [png_b64(x) if isinstance(x, cairo.ImageSurface)
                else base64.b64encode(x).decode('ascii') if isinstance(x, bytes)
                else x for x in self.takes]

def new_layer(name=None):
    if name is None:
        # translated at creation, like scene names: the default is DATA
        name = _t('Layer %d') % 1
    return {'name': name, 'visible': True, 'mouth_slots': None, 'runs': []}

def new_scene(name='Scene 1', length=None, fps=12):
    if length is None:
        # eight seconds of runway at the project's own speed
        length = fps * 8
    return {'name': name, 'length': length, 'layers': [new_layer()], 'sounds': [None, None], 'markers': []}

class AnimationDocument:

    def __init__(self, canvas=(320, 240), fps=12, boil_every=2, palette=None, palette_only=False, cels=None, scenes=None, extra=None):
        self.canvas = tuple(canvas)
        self.fps = fps
        self.boil_every = boil_every
        self.palette = list(palette or [])
        self.palette_only = bool(palette_only)
        self.cels = list(cels or [])
        self.scenes = list(scenes or [new_scene(fps=fps)])
        self._extra = extra or {}
        self.next_cel = max([c.id for c in self.cels] + [0]) + 1

    def add_cel(self, name=None, source=None):
        if len(self.cels) >= CEL_MAX:
            return None
        s = surface(*self.canvas)
        c = Cel(self.next_cel, name or _t('Drawing %d') % self.next_cel, [s], *self.canvas)
        self.next_cel += 1
        if source is not None:
            ctx = cairo.Context(s)
            ctx.set_operator(cairo.OPERATOR_SOURCE)
            ctx.set_source_surface(source)
            ctx.paint()
        self.cels.append(c)
        return c

    def cel(self, id_):
        return next((c for c in self.cels if c.id == id_), None)

    def serial(self):
        scenes = copy.deepcopy(self.scenes)
        for scene in scenes:
            scene['sounds'] = [
                (dict(sound, path=_portable_path(sound['path']))
                 if sound else None)
                for sound in scene.get('sounds', [])]
        return dict(self._extra, format=FORMAT, app='animation', canvas=list(self.canvas), fps=self.fps, boil_every=self.boil_every, palette=self.palette, palette_only=self.palette_only, cels=[c.serial() for c in self.cels], scenes=scenes)

    def bytes(self):
        return json.dumps(self.serial(), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

    @classmethod
    def parse(cls, raw, strict=True):
        """Rebuild a film from plain data.

        `strict` decodes every take to find damage, which is the whole
        point when the bytes came off a disk somebody else's program may
        have touched. Undo and redo hand back bytes this app serialised
        seconds ago, and validating those cost 125ms an undo on a
        hundred-and-fifty-drawing film — for an answer known in advance.
        Takes are stored encoded either way and decode lazily, and
        Cel.decoded already turns anything unreadable into blank paper.
        """
        if not isinstance(raw, dict) or raw.get('format') != FORMAT or raw.get('app') != 'animation':
            raise ValueError('unrecognized Animation document')
        canvas = tuple(raw.get('canvas', (320, 240)))
        fps = int(raw.get('fps', 12))
        if canvas not in CANVAS_PRESETS or fps not in SPF:
            raise ValueError('unrecognized Animation settings')
        reports = []
        cels = []
        for item in raw.get('cels', [])[:CEL_MAX]:
            try:
                takes = []
                hurt = False
                for encoded in item['takes'][:TAKE_MAX]:
                    if not strict:
                        takes.append(encoded)
                        continue
                    # Strict decode: png_surface swallows a bad PNG into a
                    # blank, which read as silent acceptance of a corrupt
                    # take. A take that will not decode becomes a blank
                    # placeholder AND the load says so — the drawing stays,
                    # the project opens, nothing is dropped silently.
                    try:
                        decoded = base64.b64decode(encoded, validate=True)
                        if not png_intact(decoded, *canvas):
                            cairo.ImageSurface.create_from_png(io.BytesIO(decoded))
                        takes.append(encoded)
                    except Exception:
                        hurt = True
                        takes.append(png_b64(surface(*canvas)))
                if not takes:
                    hurt = True
                    takes = [png_b64(surface(*canvas))]
                if hurt:
                    reports.append(_t('One damaged drawing was replaced.'))
                cels.append(Cel(int(item['id']), str(item.get('name', _t('Drawing'))), takes, *canvas, {k: v for k, v in item.items() if k not in {'id', 'name', 'takes'}}))
            except Exception:
                reports.append(_t('One damaged drawing was replaced.'))
        scenes = []
        for s in raw.get('scenes', [])[:SCENE_MAX]:
            try:
                length = max(1, min(SCENE_FRAME_MAX, int(s['length'])))
                layers = []
                for l in s.get('layers', [])[:LAYER_MAX]:
                    layers.append(dict(l, runs=normalize_runs(l.get('runs', []), length)))
                sounds = list(s.get('sounds', []))[:2] + [None, None]
                sounds = sounds[:2]
                for snd in sounds:
                    if snd:
                        snd['path'] = _resolve_path(snd.get('path', ''))
                    if snd and (not os.path.exists(snd.get('path', ''))):
                        reports.append(_t('A sound file is missing: %s') % snd.get('path', ''))
                scenes.append(dict(s, name=str(s.get('name', _t('Scene'))), length=length, layers=layers or [new_layer()], sounds=sounds, markers=list(s.get('markers', []))))
            except Exception:
                reports.append(_t('One damaged scene was replaced.'))
        known = {'format', 'app', 'canvas', 'fps', 'boil_every', 'palette', 'palette_only', 'cels', 'scenes'}
        return (cls(canvas, fps, max(1, int(raw.get('boil_every', 2))), raw.get('palette', [])[:16], raw.get('palette_only', False), cels, scenes or [new_scene()], {k: v for k, v in raw.items() if k not in known}), reports)

def _portable_path(path):
    """A sound's path as the FILE should carry it.

    Sounds are referenced, not embedded (spec §7), so a film that names
    /home/ben/Music/take.wav loses its audio the moment the project moves
    to a stick or a different home. A path inside NB_HOME is stored
    relative to it, which makes a whole home portable; anything outside
    stays absolute, because there is nothing else honest to say about it.
    """
    try:
        inside = os.path.relpath(path, NB_HOME)
    except ValueError:
        return path
    if inside.startswith(os.pardir) or os.path.isabs(inside):
        return path
    return inside


def _resolve_path(path):
    """The in-memory form: always absolute, so every existence check,
    decode and export sees a real file."""
    if not path or os.path.isabs(path):
        return path
    return os.path.join(NB_HOME, path)


def save_document(doc, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nbapp.atomic_write_json(path, doc.serial())

def load_store(path):
    """Load the owned recovery store, preserving damaged/foreign bytes."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        return (AnimationDocument(), False, [])
    except Exception:
        nbapp.preserve_damaged(path)
        return (AnimationDocument(), True, [_t('The damaged Animation store was preserved.')])
    try:
        doc, reports = AnimationDocument.parse(raw)
        return (doc, False, reports)
    except Exception:
        nbapp.quarantine_unrecognized(path)
        return (AnimationDocument(), True, [_t('This was not an Animation store, so it was set aside.')])


def open_document(path):
    """Read a user document without ever moving, rewriting, or quarantining it."""
    try:
        with open(path, 'r', encoding='utf-8') as source:
            raw = json.load(source)
        document, reports = AnimationDocument.parse(raw)
    except Exception:
        return (None, [_t('This file could not be read as an Animation project. It was left untouched.')])
    return (document, reports)

class Sheet:

    def __init__(self, doc, scene=0):
        self.doc = doc
        self.scene_i = scene
        self.clipboard = None

    @property
    def scene(self):
        return self.doc.scenes[self.scene_i]

    def stamp(self, layer, run, replace=False):
        runs = self.scene['layers'][layer]['runs']
        kept = runs
        if replace:
            kept = [r for r in runs if r['start'] + r['len'] <= run['start'] or r['start'] >= run['start'] + run['len']]
        # Validate on a candidate list FIRST: normalize_runs raises on overlap,
        # and an append-then-normalize left the overlapping run behind when it
        # did (the suite's F2 property sweep caught the corrupt sheet).
        candidate = normalize_runs(kept + [run], self.scene['length'])
        runs[:] = candidate

    def extend(self, layer, frame):
        r = run_at(self.scene['layers'][layer]['runs'], frame)
        if not r:
            return False
        end = r['start'] + r['len']
        if end >= self.scene['length'] or run_at(self.scene['layers'][layer]['runs'], end):
            return False
        r['len'] += 1
        return True

    def shorten(self, layer, frame):
        r = run_at(self.scene['layers'][layer]['runs'], frame)
        if not r:
            return False
        if r['len'] == 1:
            self.scene['layers'][layer]['runs'].remove(r)
        else:
            r['len'] -= 1
        return True

    def split(self, layer, frame):
        runs = self.scene['layers'][layer]['runs']
        r = run_at(runs, frame)
        if not r or frame == r['start']:
            return False
        right = copy.deepcopy(r)
        right['start'] = frame
        right['len'] = r['start'] + r['len'] - frame
        r['len'] = frame - r['start']
        runs.append(right)
        runs.sort(key=lambda x: x['start'])
        return True

    def clear(self, layer, start, end):
        runs = self.scene['layers'][layer]['runs']
        out = []
        for r in runs:
            a, b = (r['start'], r['start'] + r['len'])
            if b <= start or a >= end:
                out.append(r)
                continue
            if a < start:
                q = copy.deepcopy(r)
                q['len'] = start - a
                out.append(q)
            if b > end:
                q = copy.deepcopy(r)
                q['start'] = end
                q['len'] = b - end
                out.append(q)
        runs[:] = out

    def copy_block(self, layers, start, end):
        block = []
        for li in layers:
            for r in self.scene['layers'][li]['runs']:
                if r['start'] >= start and r['start'] + r['len'] <= end:
                    block.append((li, r['start'] - start, copy.deepcopy(r)))
        self.clipboard = (end - start, block)
        return self.clipboard

    def paste(self, at, repeats=1):
        if not self.clipboard:
            return False
        width, block = self.clipboard
        if not block:
            # copy_block takes whole exposures only, so a selection lying
            # inside one longer hold copies a WIDTH and no runs. Pasting
            # that used to report success and change nothing, which is
            # indistinguishable from a feature that does not work.
            return False
        candidate = []
        for n in range(repeats):
            for li, off, r in block:
                q = copy.deepcopy(r)
                q['start'] = at + n * width + off
                candidate.append((li, q))
        for li, r in candidate:
            if r['start'] < 0 or r['start'] + r['len'] > self.scene['length'] or any((not (x['start'] + x['len'] <= r['start'] or x['start'] >= r['start'] + r['len']) for x in self.scene['layers'][li]['runs'])):
                return False
        for li, r in candidate:
            self.scene['layers'][li]['runs'].append(r)
            self.scene['layers'][li]['runs'].sort(key=lambda x: x['start'])
        return True

    def insert(self, at, count):
        if self.scene['length'] + count > SCENE_FRAME_MAX:
            return False
        for l in self.scene['layers']:
            for r in l['runs']:
                if r['start'] >= at:
                    r['start'] += count
                elif r['start'] + r['len'] > at:
                    r['len'] += count
        self.scene['length'] += count
        return True

    def remove(self, at, count):
        end = min(self.scene['length'], at + count)
        for li in range(len(self.scene['layers'])):
            self.clear(li, at, end)
        delta = end - at
        for l in self.scene['layers']:
            for r in l['runs']:
                if r['start'] >= end:
                    r['start'] -= delta
        self.scene['length'] -= delta
        return delta > 0

    def slide(self, layer, left, right):
        runs = self.scene['layers'][layer]['runs']
        a = run_at(runs, left)
        b = run_at(runs, right)
        if not a or not b or a is b or (a['cel'] != b['cel']):
            return False
        start = a['start'] + a['len']
        gap = b['start'] - start
        if gap <= 0:
            return False
        # Every frame of the gap must be uncovered BEFORE the first stamp, or
        # a run sitting between the two ends would make a mid-slide stamp
        # raise and leave the fill half-done.
        if any(run_at(runs, start + i) for i in range(gap)):
            return False
        for i in range(gap):
            t = (i + 1) / (gap + 1)
            self.stamp(layer, make_run(a['cel'], start + i, 1, round(a['dx'] + (b['dx'] - a['dx']) * t), round(a['dy'] + (b['dy'] - a['dy']) * t), a.get('take', 0)))
        return True

    def ensure_drawing(self, layer, frame, duplicate=False, force_new=False):
        runs = self.scene['layers'][layer]['runs']
        r = run_at(runs, frame)
        if r and not duplicate and not force_new:
            return (self.doc.cel(r['cel']), r)
        src = self.doc.cel(r['cel']).decoded(0) if duplicate and r else None
        held_end = r['start'] + r['len'] if r else None
        if r:
            self.split(layer, frame)
            self.clear(layer, frame, held_end)
        next_start = held_end if held_end is not None else min([x['start'] for x in runs if x['start'] > frame] + [self.scene['length']])
        c = self.doc.add_cel(source=src)
        if c:
            self.stamp(layer, make_run(c.id, frame, next_start - frame))
        return (c, run_at(runs, frame))

def wobble_take(source, cel_id, take_no, strength):
    """Deterministic coherent 6-pixel-grid value-noise nearest remap."""
    w, h = (source.get_width(), source.get_height())
    src = bytes(source.get_data())
    stride = source.get_stride()
    out = surface(w, h)
    dst = out.get_data()
    seed = '%d:%d:%.3f' % (cel_id, take_no, strength)
    noise_cache = ({}, {})

    def noise(gx, gy, axis):
        """Return one deterministic grid value, constructing its RNG once."""
        key = (gx, gy)
        cached = noise_cache[axis]
        if key not in cached:
            point_seed = seed + ':%d:%d:%d' % (gx, gy, axis)
            cached[key] = random.Random(point_seed).uniform(-strength,
                                                            strength)
        return cached[key]

    def field(x, y, axis):
        gx, gy = (x // 6, y // 6)
        tx, ty = (x % 6 / 6.0, y % 6 / 6.0)
        a = noise(gx, gy, axis) * (1 - tx) + noise(gx + 1, gy, axis) * tx
        b = noise(gx, gy + 1, axis) * (1 - tx) + noise(gx + 1, gy + 1, axis) * tx
        return a * (1 - ty) + b * ty
    # The field is only defined on a six-pixel grid, so its four corner
    # values are the same for every pixel in a six-wide run — the plain
    # double loop looked them up per pixel, twenty-four dictionary hits and
    # two get_stride() calls deep, for a picture that is mostly blank paper.
    # 306ms for one take at 320x240, 1263ms at 640x480, and the card makes up
    # to four of them while the window sits frozen. Same arithmetic in the
    # same order, hoisted: the pixels are byte-identical.
    out_stride = out.get_stride()
    steps = [i / 6.0 for i in range(6)]
    # A displacement is never larger than the strength, so a destination
    # further than that from any ink can only sample blank paper — and blank
    # is what the new surface already holds. Bounding the work to the ink is
    # therefore exact, and it is the difference between a mouth cel costing a
    # tenth of a second and costing nothing. A cel with an opaque background
    # has ink everywhere and correctly gets no discount.
    reach = int(math.ceil(strength)) + 1
    top, bottom, left, right = h, -1, w, -1
    for y in range(h):
        row_at = y * stride
        alpha = bytes(src[row_at + 3:row_at + w * 4:4])
        lead = len(alpha) - len(alpha.lstrip(b'\x00'))
        if lead == len(alpha):
            continue
        top = min(top, y)
        bottom = y
        left = min(left, lead)
        right = max(right, len(alpha.rstrip(b'\x00')) - 1)
    if bottom < 0:
        out.mark_dirty()
        return out
    top, bottom = max(0, top - reach), min(h - 1, bottom + reach)
    left, right = max(0, left - reach), min(w - 1, right + reach)
    for y in range(top, bottom + 1):
        gy, ty = y // 6, y % 6 / 6.0
        row = bytearray(out_stride)
        for gx in range(left // 6, right // 6 + 1):
            n00, n10 = noise(gx, gy, 0), noise(gx + 1, gy, 0)
            n01, n11 = noise(gx, gy + 1, 0), noise(gx + 1, gy + 1, 0)
            m00, m10 = noise(gx, gy, 1), noise(gx + 1, gy, 1)
            m01, m11 = noise(gx, gy + 1, 1), noise(gx + 1, gy + 1, 1)
            base = gx * 6
            for step in range(min(6, w - base)):
                x = base + step
                if x < left or x > right:
                    continue
                tx = steps[step]
                fx = ((n00 * (1 - tx) + n10 * tx) * (1 - ty) +
                      (n01 * (1 - tx) + n11 * tx) * ty)
                fy = ((m00 * (1 - tx) + m10 * tx) * (1 - ty) +
                      (m01 * (1 - tx) + m11 * tx) * ty)
                sx = max(0, min(w - 1, round(x - fx)))
                sy = max(0, min(h - 1, round(y - fy)))
                at = sy * stride + sx * 4
                row[x * 4:x * 4 + 4] = src[at:at + 4]
        dst[y * out_stride:(y + 1) * out_stride] = row
    out.mark_dirty()
    return out

def wav_samples(path):
    with wave.open(path, 'rb') as w:
        channels, width, rate = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError('16-bit WAV required')
    a = array.array('h')
    a.frombytes(raw)
    if channels > 1:
        a = array.array('h', (sum(a[i:i + channels]) // channels for i in range(0, len(a), channels)))
    if rate != 48000:
        a = array.array('h', (a[min(len(a) - 1, int(i * rate / 48000))] for i in range(round(len(a) * 48000 / rate))))
    return a


_AUDIO_CACHE = collections.OrderedDict()
AUDIO_CACHE_MAX = 8


def decode_samples(path, sig=None):
    """Decode a sound to 48 kHz mono s16, with a bounded signature cache."""
    key = (path, tuple(sig or ()))
    cached = _AUDIO_CACHE.get(key)
    if cached is not None:
        _AUDIO_CACHE.move_to_end(key)
        return cached
    if path.lower().endswith('.wav'):
        samples = wav_samples(path)
    else:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError(_t('This sound cannot be played on this system.'))
        process = subprocess.run(
            [ffmpeg, '-v', 'error', '-i', path, '-f', 's16le', '-ac', '1',
             '-ar', '48000', '-'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        if process.returncode:
            raise RuntimeError(process.stderr.decode('utf-8', 'replace')[-300:])
        samples = array.array('h')
        samples.frombytes(process.stdout)
    _AUDIO_CACHE[key] = samples
    while len(_AUDIO_CACHE) > AUDIO_CACHE_MAX:
        _AUDIO_CACHE.popitem(last=False)
    return samples


class AudioOut:
    """Lazy Gst appsrc output copied from Sequencer's pipeline discipline.

    Gst is imported and initialized only in start(), keeping module import and
    host construction safe on systems without multimedia packages.
    """

    def __init__(self):
        self.available = False
        self.samples_delivered = 0
        self._pipe = None
        self._src = None
        self._thread = None
        self._stop = threading.Event()

    def start(self, pull):
        self.stop()
        try:
            gi.require_version('Gst', '1.0')
            from gi.repository import Gst
            Gst.init(None)
            pipeline = Gst.parse_launch(
                'appsrc name=src is-live=true format=time block=true '
                '! audioconvert ! audioresample ! alsasink')
            source = pipeline.get_by_name('src')
            source.set_property('caps', Gst.Caps.from_string(
                'audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=1'))
            pipeline.set_state(Gst.State.PLAYING)
        except Exception:
            self.available = False
            return False
        self._pipe = pipeline
        self._src = source
        self._stop.clear()
        self.samples_delivered = 0

        def pump():
            while not self._stop.is_set():
                block = pull(1024)
                if not block:
                    break
                raw = block.tobytes()
                buffer = Gst.Buffer.new_allocate(None, len(raw), None)
                buffer.fill(0, raw)
                result = source.emit('push-buffer', buffer)
                if result != Gst.FlowReturn.OK:
                    break
                self.samples_delivered += len(block)
            source.emit('end-of-stream')

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()
        self.available = True
        return True

    def position_samples(self):
        """Samples actually PLAYED, from the pipeline clock.

        samples_delivered counts what the pump has pushed, which runs ahead
        of the speaker by the sink's buffer; a mouth stamped against the
        pushed count lands early. The pipeline's position query reports what
        has really sounded; fall back to the pushed count when the query is
        not answerable yet (first moments of a fresh pipeline)."""
        if self._pipe is not None:
            try:
                from gi.repository import Gst
                ok, position = self._pipe.query_position(Gst.Format.TIME)
                if ok and position >= 0:
                    return int(position * 48000 // Gst.SECOND)
            except Exception:
                pass
        return self.samples_delivered

    def play_once(self, samples):
        position = 0

        def pull(count):
            nonlocal position
            block = samples[position:position + count]
            position += len(block)
            return block

        return self.start(pull)

    def stop(self):
        self._stop.set()
        if self._pipe is not None:
            try:
                from gi.repository import Gst
                self._pipe.set_state(Gst.State.NULL)
            except Exception:
                pass
        self._pipe = None
        self._src = None
        self.available = False

def wav_peak(path, columns=256):
    a = wav_samples(path)
    step = max(1, len(a) // columns)
    out = []
    for i in range(0, len(a), step):
        q = a[i:i + step]
        out.extend((min(q), max(q)))
    return array.array('h', out)

def mix_s16(clips, start, count):
    out = [0] * count
    for samples, offset in clips:
        for i in range(count):
            j = start + i - offset
            if 0 <= j < len(samples):
                out[i] = max(-32768, min(32767, out[i] + samples[j]))
    return array.array('h', out)

def loudness_slots(samples, spf, quiet=0.1, loud=0.45):
    rms = []
    for i in range(0, len(samples), spf):
        q = samples[i:i + spf]
        rms.append(math.sqrt(sum((x * x for x in q)) / max(1, len(q))))
    peak = max(rms or [1]) or 1
    slots = [1 if r / peak < quiet else 2 if r / peak < loud else 3 for r in rms]
    for i in range(len(slots) - 1):
        if (i == 0 or slots[i] != slots[i - 1]) and slots[i] != slots[i + 1]:
            slots[i] = slots[i + 1]
    return slots

def slots_to_runs(slots, cel_ids):
    out = []
    for f, slot in enumerate(slots):
        cel = cel_ids[min(slot - 1, len(cel_ids) - 1)]
        if out and out[-1]['cel'] == cel:
            out[-1]['len'] += 1
        else:
            out.append(make_run(cel, f, 1))
    return out

class FrameLRU:

    def __init__(self, limit=64):
        self.limit = limit
        self.cache = collections.OrderedDict()

    def get(self, key, builder):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        value = builder()
        self.cache[key] = value
        while len(self.cache) > self.limit:
            self.cache.popitem(False)
        return value

    def clear(self):
        self.cache.clear()

def frame_key(doc, scene, frame):
    key = []
    for l in scene['layers']:
        if not l.get('visible', True):
            continue
        r = run_at(l['runs'], frame)
        if r:
            c = doc.cel(r['cel'])
            key.append((c.id, take_index(r, frame, len(c.takes), doc.boil_every), r['dx'], r['dy'], c.version))
    return tuple(key)

def composite(doc, scene, frame, paper=True):
    """The frame as the film shows it. `paper=False` leaves the ground
    transparent, which is what an onion skin needs: a neighbouring frame
    carrying its own opaque paper would simply hide the frame beneath it."""
    out = surface(*doc.canvas, white=paper)
    ctx = cairo.Context(out)
    ctx.set_antialias(cairo.ANTIALIAS_NONE)
    for l in scene['layers']:
        if not l.get('visible', True):
            continue
        r = run_at(l['runs'], frame)
        if r:
            c = doc.cel(r['cel'])
            if c:
                ctx.set_source_surface(c.decoded(take_index(r, frame, len(c.takes), doc.boil_every)), r['dx'], r['dy'])
                ctx.paint()
    return out

def _encoder_probe(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, timeout=8).stdout.decode('utf-8', 'replace')
    except Exception:
        out = ''
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 1:
            names.add(parts[1])
    return 'libx264' if 'libx264' in names else 'libopenh264' if 'libopenh264' in names else 'mpeg4'
_ENCODERS = {}

def video_encoder(ffmpeg, w, h):
    codec = _ENCODERS.setdefault(ffmpeg, _encoder_probe(ffmpeg))
    mb = max(1.2, 3 * w * h / (1280 * 720))
    args = ['-c:v', codec]
    args += ['-preset', 'veryfast', '-crf', '21'] if codec == 'libx264' else ['-b:v', '%.1fM' % (mb if codec == 'libopenh264' else mb * 1.6)]
    return args + ['-pix_fmt', 'yuv420p']

def ffmpeg_path():
    return shutil.which('ffmpeg')

def export_png_frames(doc, frames, directory, cancel=None, progress=None):
    os.makedirs(directory, exist_ok=True)
    for i, (scene, frame) in enumerate(frames):
        if cancel and cancel.is_set():
            break
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.frame-',
                                   suffix='.png')
        os.close(fd)
        composite(doc, scene, frame).write_to_png(tmp)
        os.replace(tmp, os.path.join(directory, 'frame-%04d.png' % (i + 1)))
        if progress:
            progress((i + 1) / len(frames))

def _rgb24(image):
    """The frame as raw RGB for the encoder's stdin.

    This runs once per exported frame, so a three-minute film at twelve a
    second put a Python loop around seventeen million pixels. The channels
    are already laid out at a fixed stride, and a bytearray can be written a
    channel at a time with a step, which is the same copy done in C: 26x on
    a 320x240 frame, and the bytes are identical.
    """
    image.flush()
    source = image.get_data()
    stride = image.get_stride()
    width, height = image.get_width(), image.get_height()
    output = bytearray(width * height * 3)
    span = width * 3
    for y in range(height):
        row = bytes(source[y * stride:y * stride + width * 4])
        line = bytearray(span)
        line[0::3] = row[2::4]          # cairo keeps them blue-green-red-alpha
        line[1::3] = row[1::4]
        line[2::3] = row[0::4]
        output[y * span:(y + 1) * span] = line
    return output


def export_video(doc, frames, path, width, height, native=False, cancel=None,
                 progress=None, audio_specs=None):
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError(_t('Movies cannot be exported on this system.'))
    fps = doc.fps if native else CONFORM_FPS[doc.fps]
    k = max(1, min(width // doc.canvas[0], height // doc.canvas[1]))
    partial = path + '.partial.mp4'
    progress_file = tempfile.NamedTemporaryFile(
        prefix='animation-progress-', suffix='.txt', delete=False)
    progress_path = progress_file.name
    progress_file.close()
    stderr_file = tempfile.NamedTemporaryFile(
        prefix='animation-stderr-', suffix='.txt', delete=False)
    stderr_path = stderr_file.name
    vf = 'scale=iw*%d:ih*%d:flags=neighbor,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=%d' % (k, k, width, height, fps)
    args = [ff, '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s',
            '%dx%d' % doc.canvas, '-r', str(doc.fps), '-i', '-']
    for spec in audio_specs or []:
        args += ['-i', spec['path']]
    args += ['-vf', vf]
    if audio_specs:
        filters = []
        labels = []
        for index, spec in enumerate(audio_specs, 1):
            label = 'a%d' % index
            trim = 'atrim=start_sample=%d' % spec['in_smp']
            if spec['out_smp']:
                trim += ':end_sample=%d' % spec['out_smp']
            filters.append('[%d:a]%s,adelay=%dS[%s]' %
                           (index, trim, spec['delay_smp'], label))
            labels.append('[%s]' % label)
        filters.append('%samix=inputs=%d:normalize=0[aout]' %
                       (''.join(labels), len(labels)))
        args += ['-filter_complex', ';'.join(filters), '-map', '0:v',
                 '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k']
    args += video_encoder(ff, width, height) + ['-progress', progress_path,
                                                partial]
    stderr_handle = stderr_file
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=stderr_handle)
    try:
        for i, (scene, frame) in enumerate(frames):
            if cancel and cancel.is_set():
                raise InterruptedError()
            proc.stdin.write(_rgb24(composite(doc, scene, frame)))
            if progress:
                progress((i + 1) / len(frames))
        proc.stdin.close()
        rc = proc.wait()
        stderr_handle.close()
        with open(stderr_path, 'rb') as error_source:
            err = error_source.read()
        if rc:
            raise RuntimeError(err.decode('utf-8', 'replace')[-500:])
        os.replace(partial, path)
    except Exception:
        proc.kill()
        stderr_handle.close()
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    finally:
        for scratch in (progress_path, stderr_path):
            try:
                os.unlink(scratch)
            except OSError:
                pass


def export_gif(doc, frames, path, scale, cancel=None, progress=None):
    """Two-pass bayer GIF export through palettegen and paletteuse."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(_t('Movies cannot be exported on this system.'))
    scratch = tempfile.mkdtemp(prefix='animation-gif-')
    palette = os.path.join(scratch, 'palette.png')
    partial = path + '.partial.gif'
    try:
        raw_fd, raw_path = tempfile.mkstemp(dir=scratch, prefix='frames-',
                                            suffix='.rgb')
        with os.fdopen(raw_fd, 'wb') as raw:
            for index, (scene, frame) in enumerate(frames):
                if cancel and cancel.is_set():
                    raise InterruptedError()
                raw.write(_rgb24(composite(doc, scene, frame)))
                if progress:
                    progress(.45 * (index + 1) / len(frames))
        common = ['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s',
                  '%dx%d' % doc.canvas, '-r', str(doc.fps), '-i', raw_path]
        scale_filter = 'scale=iw*%d:ih*%d:flags=neighbor' % (scale, scale)
        subprocess.run([ffmpeg, '-y'] + common + ['-vf',
                       scale_filter + ',palettegen=stats_mode=diff', palette],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        subprocess.run([ffmpeg, '-y'] + common + ['-i', palette,
                       '-lavfi', scale_filter +
                       '[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=2',
                       '-loop', '0', partial], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.replace(partial, path)
        if progress:
            progress(1.0)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.exists(partial):
            os.unlink(partial)

class StackHistory:

    def __init__(self, app):
        self.app = app

    def can_undo(self):
        return bool(self.app._undo)

    def can_redo(self):
        return bool(self.app._redo)

    def undo_label(self):
        return self.app._undo[-1][0] if self.app._undo else None

    def redo_label(self):
        return self.app._redo[-1][0] if self.app._redo else None

    def undo(self):
        return self.app._history_apply(False)

    def redo(self):
        return self.app._history_apply(True)

class Animation(nbapp.AppWindow):
    """GTK shell; the document engine above remains the output authority."""
    app_name = 'Animation'
    menus = ('File', 'Edit', 'View', 'Paint', 'Timeline', 'Scene', 'Drawing',
             'Layer', 'Sound')

    def __init__(self, path=None):
        super().__init__()
        self.set_title(_t('Animation'))
        self.set_default_size(1024, 722)
        self.get_style_context().add_class('animation')
        self.doc_path = path
        self.doc = AnimationDocument()
        self.scene_i = 0
        self.layer_i = 0
        self.playhead = 0
        self.zoom = 1
        self._fitted = False
        self._undo = []
        self._redo = []
        self.history = StackHistory(self)
        self._dirty = False
        self._doc_dirty = False
        self._save_error = None
        self._guard_bypass = False
        self._store_read_only = False
        self._save_timer = 0
        self._flash_timer = 0
        self._alive = True
        self._cache = FrameLRU()
        self._scene_thumbs = {}
        self._compact_source = 0
        self._cel_thumbs = {}
        self._playing = False
        self._tick = 0
        self._reports = []
        self.tool = 'pencil'
        self.previous_tool = 'pencil'
        self.color = '#1A1916'
        self.size = 3
        self.shape = 'square'
        self.pattern = 'solid'
        self.symx = False
        self.symy = False
        self.grid = False
        self.onion = 0
        self.loop = False
        self.stamp_mouths = False
        self.active_take = {}
        self.selection = None
        self._selected_sound = None
        self.column_width = 6
        self.view_origin = 0
        self.selection_layers = None
        self._prompt_layer = None
        self._worker_generation = 0
        self._workers = []
        self._cancel = threading.Event()
        self._playing_started = 0.0
        self._play_origin = 0
        self.audio = AudioOut()
        self._audio_clips = []
        self._audio_position = 0
        self._mouth_pass_open = False
        if path:
            opened, reports = open_document(path)
            if opened is not None:
                self.doc = opened
                self._reports = reports
            else:
                self.doc_path = None
                self._reports = reports
        elif os.path.exists(STORE_FILE):
            self.doc, self._store_read_only, self._reports = load_store(STORE_FILE)
            remembered = self.doc._extra.pop('doc_path', None)
            unsaved = bool(self.doc._extra.pop('doc_dirty', False))
            if remembered and not self._store_read_only:
                resolved = _resolve_path(remembered)
                if os.path.exists(resolved):
                    self.doc_path = resolved
                    # the title is how a person knows WHICH film they are in
                    self.set_title(_t('Animation') + ' - ' +
                                   os.path.basename(resolved))
                    self._doc_dirty = unsaved
            self._restore_session(self.doc._extra.pop('session', None))
        self.sheet = Sheet(self.doc)
        self._build()
        if self.doc_path and self._doc_dirty:
            # carried across the restart, so Ctrl+S is an obvious thing to do
            self.save_chip.set_text(_t('Not saved to file'))
        for scene in self.doc.scenes:
            for sound in scene['sounds']:
                if sound and os.path.exists(sound.get('path', '')) and \
                        not sound.get('duration_smp'):
                    sound.setdefault('_peak_token', 0)
                    self._start_peak_worker(sound)
        self.connect('delete-event', self._on_delete)
        self.connect('destroy', self._on_destroy)

    def _restore_session(self, session):
        """Put the person back where they were, or somewhere sane.

        Every value is clamped against the film as it is NOW: a scene that
        was deleted, a layer that no longer exists or a frame past the end
        must land somewhere valid rather than raising on the first repaint.
        """
        if not isinstance(session, dict):
            return
        scenes = self.doc.scenes
        self.scene_i = max(0, min(len(scenes) - 1,
                                  int(session.get('scene', 0) or 0)))
        scene = scenes[self.scene_i]
        self.sheet = Sheet(self.doc, self.scene_i)
        self.playhead = max(0, min(scene['length'] - 1,
                                   int(session.get('frame', 0) or 0)))
        self.layer_i = max(0, min(len(scene['layers']) - 1,
                                  int(session.get('layer', 0) or 0)))
        self.view_origin = max(0, min(scene['length'] - 1,
                                      int(session.get('origin', 0) or 0)))
        tool = session.get('tool')
        if any(tool == name for name, _label, _key in TOOLS):
            self.tool = self.previous_tool = tool
        colour = session.get('colour')
        if isinstance(colour, str) and _HEX.match(colour or ''):
            self.color = colour
        size = session.get('size')
        if isinstance(size, int) and 1 <= size <= 192:
            self.size = size
        columns = session.get('columns')
        if columns in (3, 6, 12, 24):
            self.column_width = columns
        onion = session.get('onion')
        if onion in (0, 1, 2):
            self.onion = onion
        zoom = session.get('zoom')
        if isinstance(zoom, (int, float)) and zoom in ZOOM_STEPS:
            self.zoom = zoom
            self._fitted = True

    def _build(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body = Gtk.Box()
        root.pack_start(body, True, True, 0)
        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        dock.set_size_request(240, -1)
        dock.pack_start(Gtk.Label(label=_t('Tools'), xalign=0), False, False, 4)
        tool_grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        for index, (tool, name, key) in enumerate(TOOLS):
            button = self._mark_btn(
                tool, TOOL_HINTS[tool], self._choose_tool,
                label=name, key=key, toggle=True, callback_arg=tool)
            button.set_active(tool == self.tool)
            tool_grid.attach(button, index % 2, index // 2, 1, 1)
        dock.pack_start(tool_grid, False, False, 0)
        # Order is what someone reaches for, most often first. The dock
        # scrolls, and colour used to sit 641px down a 406px viewport —
        # the control a drawing app is USED through, below the fold at
        # every screen size, behind a scrollbar nobody had reason to drag.
        self._build_brush_group(dock)
        self._build_colour_group(dock)
        self._build_shape_group(dock)
        self._build_pattern_group(dock)
        self._build_mirror_group(dock)
        self._build_project_palette(dock)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(240, -1)
        scroll.add(dock)
        body.pack_start(scroll, False, False, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_can_focus(True)
        self.canvas.get_accessible().set_name(_t('Animation canvas'))
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                               Gdk.EventMask.BUTTON_RELEASE_MASK |
                               Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.connect('draw', self._draw_canvas)
        self.canvas.connect('button-press-event', self._canvas_press)
        self.canvas.connect('motion-notify-event', self._canvas_motion)
        self.canvas.connect('button-release-event', self._canvas_release)
        mat = Gtk.EventBox()
        mat.get_style_context().add_class('animation-mat')
        mat.add(self.canvas)
        self.canvas_scroll = Gtk.ScrolledWindow()
        self.canvas_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                      Gtk.PolicyType.AUTOMATIC)
        self.canvas_scroll.add(mat)
        body.pack_start(self.canvas_scroll, True, True, 0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.set_size_request(240, -1)
        side.pack_start(Gtk.Label(label=_t('Drawings'), xalign=0), False, False, 8)
        self.cel_list = Gtk.ListBox()
        self.cel_list.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                 Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.cel_list.connect('button-press-event', self._cel_list_press)
        self.cel_list.connect('row-activated', self._cel_row_activated)
        self.cel_list.connect('row-selected', self._cel_row_selected)
        self.cel_list.connect('button-release-event', self._cel_list_release)
        cel_scroll = Gtk.ScrolledWindow()
        cel_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cel_scroll.add(self.cel_list)
        side.pack_start(cel_scroll, True, True, 0)
        self.takes_box = Gtk.Box(spacing=2)
        side.pack_start(self.takes_box, False, False, 6)
        side.pack_start(Gtk.Label(label=_t('Layers'), xalign=0), False, False, 8)
        self.layer_list = Gtk.ListBox()
        self.layer_list.connect('row-selected', self._layer_row_selected)
        self.layer_list.connect('row-activated', lambda *_a: self._rename_layer_prompt())
        side.pack_start(self.layer_list, False, False, 0)
        layer_actions = Gtk.Box(homogeneous=True)
        # A glyph is not a name: GTK derives the accessible name from the
        # label, so '+' reads to a screen reader as "plus". Each of these
        # says what it DOES, in the tooltip and to assistive technology.
        self._layer_buttons = {}
        for label, tip, callback in (('+', 'New Layer', self._add_layer),
                                     ('-', 'Delete Layer', self._delete_layer),
                                     ('↑', 'Move Layer Up', self._raise_layer),
                                     ('↓', 'Move Layer Down', self._lower_layer)):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(_t(tip))
            button.get_accessible().set_name(_t(tip))
            button.connect('clicked', callback)
            layer_actions.pack_start(button, True, True, 0)
            self._layer_buttons[tip] = button
        side.pack_start(layer_actions, False, False, 0)
        body.pack_start(side, False, False, 0)
        self.timeline = Gtk.DrawingArea()
        self.timeline.set_can_focus(True)
        self.timeline.get_accessible().set_name(_t('Exposure sheet timeline'))
        self.timeline.set_size_request(-1, 244)
        self.timeline.connect('draw', self._draw_timeline)
        self.timeline.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                 Gdk.EventMask.BUTTON_RELEASE_MASK |
                                 Gdk.EventMask.POINTER_MOTION_MASK)
        self.timeline.connect('button-press-event', self._timeline_press)
        self.timeline.set_has_tooltip(True)
        self.timeline.connect('query-tooltip', self._timeline_tooltip)
        self.timeline.connect('motion-notify-event', self._timeline_motion)
        self.timeline.connect('button-release-event', self._timeline_release)
        target = Gtk.TargetEntry.new('application/x-animation-cel',
                                     Gtk.TargetFlags.SAME_APP, 0)
        self.timeline.drag_dest_set(Gtk.DestDefaults.ALL, [target],
                                    Gdk.DragAction.COPY)
        self.timeline.connect('drag-motion', self._timeline_drag_motion)
        self.timeline.connect('drag-data-received',
                              self._timeline_drag_data_received)
        root.pack_start(self.timeline, False, False, 0)
        status = Gtk.Box()
        self.hint = Gtk.Label(label=_t('Drag to draw. Square tip, hard edges.'), xalign=0)
        self.readout = Gtk.Label(label='0:00+00')
        # "position / length": two runs of digits either side of a slash.
        # In a right-to-left session the pair reorders and the film reads
        # as though it were eleven seconds long and playing at zero — the
        # transport draws the same figures with cairo and keeps them in
        # order, so the two disagreed on screen.
        self.readout.set_direction(Gtk.TextDirection.LTR)
        self.scene_status = Gtk.Label(label='')
        self.zoom_label = Gtk.Label(label='100%')
        self.save_chip = Gtk.Label(label=_t('Saved'))
        status.pack_start(self.hint, True, True, 8)
        status.pack_start(self.readout, False, False, 8)
        status.pack_start(self.scene_status, False, False, 8)
        zoom_out = Gtk.Button(label='−')
        zoom_out.set_tooltip_text(_t('Zoom Out'))
        zoom_out.get_accessible().set_name(_t('Zoom Out'))
        zoom_out.connect('clicked', lambda *_: self._zoom_step(-1))
        zoom_in = Gtk.Button(label='+')
        zoom_in.set_tooltip_text(_t('Zoom In'))
        zoom_in.get_accessible().set_name(_t('Zoom In'))
        zoom_in.connect('clicked', lambda *_: self._zoom_step(1))
        zoom_fit = Gtk.Button(label=_t('Fit'))
        zoom_fit.connect('clicked', lambda *_: self._fit_canvas())
        status.pack_start(zoom_out, False, False, 0)
        status.pack_start(self.zoom_label, False, False, 4)
        status.pack_start(zoom_in, False, False, 0)
        status.pack_start(zoom_fit, False, False, 0)
        status.pack_start(self.save_chip, False, False, 8)
        root.pack_start(status, False, False, 4)
        self.content.pack_start(root, True, True, 0)
        self._refresh_lists()
        self._update_playhead()

    def _group_title(self, dock, text, trailing=None):
        label = Gtk.Label(label=_t(text), xalign=0)
        label.get_style_context().add_class('animation-group')
        if trailing is None:
            dock.pack_start(label, False, False, 4)
            return
        # A value belongs on the line that names it. Its own row cost the
        # dock 23px, and the dock is where the colour palette has to fit.
        row = Gtk.Box()
        row.pack_start(label, True, True, 0)
        row.pack_start(trailing, False, False, 0)
        dock.pack_start(row, False, False, 4)

    def _mark_btn(self, kind, tip, callback, label=None, key=None,
                  toggle=False, callback_arg=None, group=None, radio=False):
        """Make Illustrator's painted-mark dock control.

        The mark is cairo-painted rather than styled: CSS cannot leave a
        supposedly dim control with a full-ink icon.  Toggle and radio forms
        retain GTK's native state and accessibility behaviour. A radio's
        FIRST button passes group=None and still gets a RadioButton — the
        None group founds the set — and set_mode(False) keeps the control
        looking like a letterpress button rather than an indicator dot.
        """
        if radio or group is not None:
            button = Gtk.RadioButton.new_from_widget(group)
            button.set_mode(False)
        elif toggle:
            button = Gtk.ToggleButton()
        else:
            button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class('animation-stepbtn')
        button.set_tooltip_text(_t(tip) + ('  (%s)' % key if key else ''))
        area = Gtk.DrawingArea()
        area.set_size_request(17, 17)
        area._animation_mark = kind
        area.connect('draw', self._draw_mark)
        box = Gtk.Box(spacing=5)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.pack_start(area, False, False, 0)
        if label is not None:
            # The key rides in the TOOLTIP, not the label — illustrator.py's
            # solution to the same nine tools in the same 240 rail. Appending
            # '  R' to a label is what cut 'Прямоугольник' in Russian.
            text = _t(label)
            word = Gtk.Label(label=text, xalign=0)
            word.set_ellipsize(Pango.EllipsizeMode.END)
            word.get_style_context().add_class('animation-marklabel')
            box.pack_start(word, True, True, 0)
            button.get_style_context().add_class('animation-wide')
        button.add(box)
        if callback_arg is None:
            button.connect('toggled' if toggle or group is not None else
                           'clicked', callback)
        else:
            button.connect('toggled' if group is not None else 'clicked',
                           callback, callback_arg)
        return button

    def _draw_mark(self, area, cr):
        """Paint the compact letterpress marks used throughout the dock."""
        width = area.get_allocated_width()
        height = area.get_allocated_height()
        if width <= 0 or height <= 0:
            return False
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        colour = '#1A1916' if area.is_sensitive() else '#B3AD9E'
        cr.set_source_rgb(*[part / 255.0 for part in _rgb255(colour)])
        cr.set_line_width(1.6)
        kind = area._animation_mark
        if kind == 'select':
            cr.move_to(3, 2)
            cr.line_to(12, 10)
            cr.line_to(8, 11)
            cr.line_to(6, 15)
            cr.close_path()
            cr.stroke()
        elif kind in ('pencil', 'brush', 'picker'):
            cr.move_to(3, 14)
            cr.line_to(12, 5)
            if kind == 'pencil':
                cr.line_to(14, 3)
                cr.line_to(12, 2)
            elif kind == 'brush':
                cr.curve_to(13, 7, 15, 9, 13, 12)
                cr.curve_to(11, 14, 8, 13, 7, 11)
            else:
                cr.line_to(14, 7)
                cr.move_to(11, 4)
                cr.arc(12, 3, 2, 0, 2 * math.pi)
            cr.stroke()
        elif kind == 'eraser':
            cr.save()
            cr.translate(8.5, 8.5)
            cr.rotate(-0.55)
            cr.rectangle(-4.5, -3, 9, 6)
            cr.stroke()
            cr.restore()
        elif kind == 'fill':
            cr.move_to(3, 7)
            cr.line_to(9, 3)
            cr.line_to(14, 9)
            cr.line_to(8, 13)
            cr.close_path()
            cr.stroke()
            cr.arc(14, 14, 1.5, 0, 2 * math.pi)
            cr.fill()
        elif kind == 'line':
            cr.move_to(2, 14)
            cr.line_to(15, 2)
            cr.stroke()
        elif kind == 'rect':
            cr.rectangle(2.5, 3.5, 12, 10)
            cr.stroke()
        elif kind == 'ellipse':
            cr.save()
            cr.translate(8.5, 8.5)
            cr.scale(1, 0.72)
            cr.arc(0, 0, 6, 0, 2 * math.pi)
            cr.restore()
            cr.stroke()
        elif kind in ('tip-square', 'tip-round'):
            if kind == 'tip-square':
                cr.rectangle(4, 4, 9, 9)
            else:
                cr.arc(8.5, 8.5, 4.5, 0, 2 * math.pi)
            cr.fill()
        elif kind in ('symx', 'symy'):
            cr.save()
            cr.set_line_width(1)
            cr.set_dash([2, 2])
            if kind == 'symx':
                cr.move_to(8.5, 0)
                cr.line_to(8.5, 17)
            else:
                cr.move_to(0, 8.5)
                cr.line_to(17, 8.5)
            cr.stroke()
            cr.restore()
            for side in (-1, 1):
                if kind == 'symx':
                    x = 8.5 + side * 2
                    cr.move_to(x, 4)
                    cr.line_to(x + side * 4, 8.5)
                    cr.line_to(x, 13)
                else:
                    y = 8.5 + side * 2
                    cr.move_to(4, y)
                    cr.line_to(8.5, y + side * 4)
                    cr.line_to(13, y)
                cr.close_path()
                cr.fill()
        elif kind.startswith('pattern-'):
            cr.rectangle(2, 2, 13, 13)
            cr.stroke()
            pattern = kind[8:]
            for y in range(4, 14, 3):
                for x in range(4, 14, 3):
                    paint = pattern == 'solid'
                    paint = paint or pattern == 'checker' and (x + y) % 2 == 0
                    paint = paint or pattern == 'sparse' and x % 2 == 0 and y % 2 == 0
                    if paint:
                        cr.rectangle(x, y, 2, 2)
            cr.fill()
        return False

    def _ramp_cells(self):
        """Return the live x and width of each brush-ramp cell."""
        width = max(1, self.ramp_area.get_allocated_width())
        step = width / 6.0
        return [(index * step, step) for index in range(6)]

    def _draw_brush_ramp(self, area, cr):
        """Paint Illustrator's small-to-large brush-tip ramp."""
        width = area.get_allocated_width()
        height = area.get_allocated_height()
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        sizes = (1, 2, 3, 6, 12, 24)
        for index, (x, cell_width) in enumerate(self._ramp_cells()):
            size = sizes[index]
            diameter = max(3, min(cell_width, height) * 0.66 *
                           (size / 24.0) ** 0.58)
            cx = x + cell_width / 2.0
            cy = height / 2.0
            cr.set_source_rgb(*[part / 255.0 for part in _rgb255('#1A1916')])
            if self.shape == 'square':
                cr.rectangle(round(cx - diameter / 2),
                             round(cy - diameter / 2),
                             round(diameter), round(diameter))
            else:
                cr.arc(cx, cy, diameter / 2, 0, 2 * math.pi)
            cr.fill()
            if size == self.size:
                cr.rectangle(round(x) + 1.5, 1.5,
                             round(cell_width) - 3, height - 3)
                cr.stroke()
        cr.set_source_rgb(*[part / 255.0 for part in _rgb255('#C9C4B6')])
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()
        return False

    def _brush_ramp_press(self, _area, event):
        sizes = (1, 2, 3, 6, 12, 24)
        for index, (x, width) in enumerate(self._ramp_cells()):
            if x <= event.x < x + width:
                self._set_size(None, sizes[index])
                self.size_lbl.set_text(_t('%d px') % self.size)
                self.ramp_area.queue_draw()
                break
        return True

    def _build_brush_group(self, dock):
        # Illustrator's group vocabulary, existing catalog keys: one title
        # over the size grid, one over the tip shapes.
        self.size_lbl = Gtk.Label(label=_t('%d px') % self.size, xalign=1)
        self._group_title(dock, 'Brush size', trailing=self.size_lbl)
        # Copied mechanism-for-mechanism from Illustrator: one painted ramp is
        # both a relative-size preview and a six-cell shortcut.
        self.ramp_area = Gtk.DrawingArea()
        self.ramp_area.set_size_request(-1, 30)
        self.ramp_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.ramp_area.connect('draw', self._draw_brush_ramp)
        self.ramp_area.connect('button-press-event', self._brush_ramp_press)
        dock.pack_start(self.ramp_area, False, False, 0)

    def _build_shape_group(self, dock):
        self._group_title(dock, 'Shapes')
        # One per row: a two-across row leaves ~85px for the word, which cut
        # the Greek and Russian tip names (ellipsis_sweep). A dock that
        # scrolls can afford the height; an unreadable word cannot be fixed
        # by the reader.
        shapes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        first = None
        self._shape_buttons = {}
        for shape, label in (('square', 'Square tip'), ('round', 'Round tip')):
            button = self._mark_btn('tip-' + shape, label, self._set_shape,
                                    label=label, callback_arg=shape,
                                    group=first, radio=True)
            if first is None:
                first = button
            button.set_active(shape == self.shape)
            shapes.pack_start(button, True, True, 0)
            self._shape_buttons[shape] = button
        dock.pack_start(shapes, False, False, 0)

    def _build_mirror_group(self, dock):
        self._group_title(dock, 'Mirror')
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Named, like every other control in the dock. These two were the
        # only ones left as a bare glyph, so the one pair of settings a
        # person could not read was also the pair furthest below the fold.
        self._mirror_buttons = {}
        for attr, tip in (('symx', 'Mirror left and right'),
                          ('symy', 'Mirror top and bottom')):
            button = self._mark_btn(attr, tip, self._set_boolean,
                                    label=tip, toggle=True, callback_arg=attr)
            button.set_active(getattr(self, attr))
            row.pack_start(button, True, True, 0)
            self._mirror_buttons[attr] = button
        dock.pack_start(row, False, False, 0)

    def _build_pattern_group(self, dock):
        self._group_title(dock, 'Pattern')
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        first = None
        self._pattern_buttons = {}
        # 'Solid' belongs to the GBA SDK, where it means a tile you cannot
        # walk through — so Japanese read the fill pattern as 'impassable'
        # and Korean as 'cannot pass'. A shared catalog is keyed on the
        # English word, not on the sense, so this app needs its own.
        for pattern, label in zip(PATTERNS,
                                  ('Solid colour', 'Checker', 'Sparse')):
            button = self._mark_btn('pattern-' + pattern, label,
                                    self._set_pattern, label=label,
                                    callback_arg=pattern, group=first,
                                    radio=True)
            if first is None:
                first = button
            button.set_active(pattern == self.pattern)
            row.pack_start(button, True, True, 0)
            self._pattern_buttons[pattern] = button
        dock.pack_start(row, False, False, 0)

    def _build_colour_group(self, dock):
        self._group_title(dock, 'Colour')
        # One DrawingArea paints all 112 swatches — illustrator.py's palette
        # mechanism (cell grid, press hit-test, hover names). Per-swatch theme
        # buttons carry a ~47px CSS minimum each, and 16 of them per row
        # forced the whole window's minimum to 1087px.
        sw, gap = 13, 1
        self._swatch_geom = (sw, gap)
        cols = 16
        rows = (len(PALETTE) + cols - 1) // cols
        self.palette_area = Gtk.DrawingArea()
        self.palette_area.set_size_request(cols * (sw + gap) - gap,
                                           rows * (sw + gap) - gap)
        self.palette_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.palette_area.connect('draw', self._draw_swatches)
        self.palette_area.connect('button-press-event', self._swatch_press)
        self.palette_area.set_has_tooltip(True)
        self.palette_area.connect('query-tooltip', self._swatch_tooltip)
        self.palette_area.get_accessible().set_name(_t('Colour swatches'))
        dock.pack_start(self.palette_area, False, False, 0)
        # The chosen colour as a thing you can see, not a hex code: a chip
        # plus the swatch's composed name (Illustrator's vocabulary).
        chip_row = Gtk.Box(spacing=6)
        self.colour_chip = Gtk.DrawingArea()
        self.colour_chip.set_size_request(24, 24)
        self.colour_chip.connect('draw', self._draw_colour_chip)
        chip_row.pack_start(self.colour_chip, False, False, 0)
        self.colour_name = Gtk.Label(label=self._colour_label(self.color),
                                     xalign=0)
        self.colour_name.set_ellipsize(Pango.EllipsizeMode.END)
        chip_row.pack_start(self.colour_name, True, True, 0)
        dock.pack_start(chip_row, False, False, 2)

    def _swatch_cell(self, x, y):
        sw, gap = self._swatch_geom
        col = int(x) // (sw + gap)
        row = int(y) // (sw + gap)
        if col < 0 or col >= 16:
            return None
        if int(x) % (sw + gap) >= sw or int(y) % (sw + gap) >= sw:
            return None
        index = row * 16 + col
        return index if 0 <= index < len(PALETTE) else None

    def _draw_swatches(self, _area, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        sw, gap = self._swatch_geom
        for index, hex_ in enumerate(PALETTE):
            row, col = divmod(index, 16)
            x, y = col * (sw + gap), row * (sw + gap)
            r, g, b = _rgb255(hex_)
            cr.set_source_rgb(r / 255, g / 255, b / 255)
            cr.rectangle(x, y, sw, sw)
            cr.fill()
            if hex_.upper() == self.color.upper():
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                cr.set_line_width(1)
                cr.rectangle(x + .5, y + .5, sw - 1, sw - 1)
                cr.stroke()
        return False

    def _swatch_press(self, _w, ev):
        index = self._swatch_cell(ev.x, ev.y)
        if index is not None:
            self._choose_colour(None, PALETTE[index])
            self.palette_area.queue_draw()
        return True

    def _swatch_tooltip(self, _w, x, y, _kb, tip):
        index = self._swatch_cell(x, y)
        if index is None:
            return False
        tip.set_text(self._palette_name(index))
        return True

    def _build_project_palette(self, dock):
        self._group_title(dock, 'Palette')
        self.project_palette_box = Gtk.Box(spacing=2)
        dock.pack_start(self.project_palette_box, False, False, 0)
        # Stacked, not side by side: the pair of labelled buttons demanded
        # 260px in one row, and translations only grow.
        add = Gtk.Button(label=_t('Add current colour'))
        add.connect('clicked', self._palette_add)
        add.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        remove = Gtk.Button(label=_t('Remove'))
        remove.connect('clicked', self._palette_remove)
        remove.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        self._palette_add_button, self._palette_remove_button = add, remove
        dock.pack_start(add, False, False, 0)
        dock.pack_start(remove, False, False, 0)
        lock = Gtk.CheckButton(label=_t('Draw with palette only'))
        lock.set_active(self.doc.palette_only)
        lock.connect('toggled', self._palette_lock)
        dock.pack_start(lock, False, False, 0)
        self._palette_lock_check = lock

    def _refresh_palette_buttons(self):
        """The palette's two buttons say what the Paint menu says.

        Both stayed lit whatever the palette held: on an empty palette
        Remove did nothing and said nothing, and with the current colour
        already in the palette — or sixteen already in it — so did Add. The
        menu gates all three conditions, so the dock and the menu disagreed
        about the same two commands. The layer buttons had this exact fault
        one sweep ago; it lives wherever a control's state is set once at
        build time.
        """
        add = getattr(self, '_palette_add_button', None)
        remove = getattr(self, '_palette_remove_button', None)
        if add is None or remove is None:
            return
        if len(self.doc.palette) >= 16:
            add_why = _t('This palette holds as many colours as it can.')
        elif self.color in self.doc.palette:
            add_why = _t('This colour is already in the palette.')
        else:
            add_why = None
        add.set_sensitive(add_why is None)
        add.set_tooltip_text(add_why or _t('Add current colour'))
        remove.set_sensitive(bool(self.doc.palette))
        remove.set_tooltip_text(_t('Remove') if self.doc.palette
                                else _t('This palette is empty.'))

    def _palette_name(self, index):
        if index < 80:
            value = index // 16
            hue = index % 16
            template = _VALUES[value][0]
            word = _HUES[hue][0]
        elif index < 96:
            template = _MUTED[0]
            word = _HUES[index - 80][0]
        elif index < 104:
            template = None
            word = _NEUTRALS[index - 96][0]
        else:
            template = None
            word = _STAPLES[index - 104][0]
        return (_t(template) % _t(word)) if template else _t(word)

    def _choose_tool(self, button, tool):
        if not button.get_active():
            return
        if tool != 'picker':
            self.previous_tool = tool
        self.tool = tool
        self._restore_tool_hint()

    def _set_size(self, _button, size):
        self.size = size

    def _ticked(self, on, text):
        """A menu item that carries its own state.

        The OS convention, which Illustrator already follows: a tick when the
        setting is on, and the same width of space when it is off, so the
        words stay in one column. nbapp pins the tick to a face that has the
        glyph — the shipped Nimbus Sans does not.

        Takes text already translated, so every string still reaches _t() as
        a literal where i18n_source_coverage can see it. Hidden behind this
        helper, six menu items would have been missing from the catalogs with
        every gate green.

        Four spaces measures exactly what a tick plus its space measures,
        16px, so the words line up in one column. Four is also how this OS
        writes the gap before an accelerator — but that gap comes AFTER a
        label, and a run of spaces at the START of one is padding. A reader
        that splits before stripping turns an unticked item into a nameless
        command bound to a key called 'Round Tip'; F23 caught exactly that,
        and the readers were what needed fixing.
        """
        return ('\u2713 ' if on else '    ') + text

    def _set_shape(self, button, shape):
        if button.get_active():
            self.shape = shape

    def _set_pattern(self, button, pattern):
        if button.get_active():
            self.pattern = pattern

    def _set_boolean(self, button, attr):
        if attr == 'symx':
            self.symx = button.get_active()
        else:
            self.symy = button.get_active()

    def _snap_colour(self, colour):
        if not self.doc.palette_only or not self.doc.palette:
            return colour
        r, g, b = _rgb255(colour)
        return min(self.doc.palette,
                   key=lambda item: sum((x - y) ** 2
                                        for x, y in zip((r, g, b), _rgb255(item))))

    def _colour_label(self, colour):
        """The swatch's composed name when the colour is on the board, else
        the honest hex (the eyedropper can pick anything)."""
        upper = colour.upper()
        for index, hex_ in enumerate(PALETTE):
            if hex_.upper() == upper:
                return self._palette_name(index)
        return upper

    def _draw_colour_chip(self, area, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        r, g, b = _rgb255(self.color)
        cr.set_source_rgb(r / 255, g / 255, b / 255)
        cr.rectangle(0, 0, area.get_allocated_width(),
                     area.get_allocated_height())
        cr.fill()
        cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
        cr.rectangle(.5, .5, area.get_allocated_width() - 1,
                     area.get_allocated_height() - 1)
        cr.stroke()
        return False

    def _choose_colour(self, _button, colour):
        self.color = self._snap_colour(colour)
        self.colour_name.set_text(self._colour_label(self.color))
        self.colour_chip.queue_draw()
        # whether this colour can be added depends on which colour it is
        self._refresh_palette_buttons()

    def _palette_add(self, *_):
        if self.color in self.doc.palette or len(self.doc.palette) >= 16:
            return
        self._snapshot(_t('Add Palette Colour'))
        self.doc.palette.append(self.color)
        self._refresh_project_palette()
        self._mark_dirty()

    def _palette_remove(self, *_):
        if not self.doc.palette:
            return
        self._snapshot(_t('Remove Palette Colour'))
        self.doc.palette.pop()
        self._refresh_project_palette()
        self._mark_dirty()

    def _palette_lock(self, button):
        self._snapshot(_t('Palette Lock'))
        self.doc.palette_only = button.get_active()
        self._choose_colour(None, self.color)
        self._mark_dirty()

    def _refresh_project_palette(self):
        for child in self.project_palette_box.get_children():
            self.project_palette_box.remove(child)
        for colour in self.doc.palette:
            button = Gtk.Button(label=' ')
            button.set_tooltip_text(colour)
            button.connect('clicked', self._choose_colour, colour)
            self.project_palette_box.pack_start(button, False, False, 0)
        self.project_palette_box.show_all()
        self._refresh_palette_buttons()

    def _thumb_frame(self, cel):
        """The part of a cel worth showing at thumbnail size.

        Framing the whole sheet renders every mouth in the film as the same
        invisible speck, so the slot picker asks a person to choose between
        pictures they cannot see. Frame the ink instead, keeping the aspect
        so a shape stays its own shape."""
        bounds = self._opaque_bounds(cel, 0)
        if not bounds:
            return (0., 0., float(cel.w), float(cel.h))
        x, y, w, h = [float(v) for v in bounds]
        pad = max(1., max(w, h) * THUMB_PAD)
        x, y, w, h = x - pad, y - pad, w + pad * 2, h + pad * 2
        least_w, least_h = THUMB_W / THUMB_ZOOM_MAX, THUMB_H / THUMB_ZOOM_MAX
        if w < least_w:
            x, w = x - (least_w - w) / 2, least_w
        if h < least_h:
            y, h = y - (least_h - h) / 2, least_h
        if w >= cel.w:
            x, w = 0., float(cel.w)
        else:
            x = max(0., min(x, cel.w - w))
        if h >= cel.h:
            y, h = 0., float(cel.h)
        else:
            y = max(0., min(y, cel.h - h))
        return (x, y, w, h)

    def _cel_thumb_surface(self, cel):
        """A 44x33 picture of take 0 — the library navigates by pictures,
        not by names."""
        return self._take_thumb_surface(cel, 0)

    def _take_thumb_surface(self, cel, take=0):
        """One take at thumbnail size.

        Every take of a drawing is framed the same way. Framing each one to
        its own ink would slide the wobble back into place and render five
        identical pictures — and the wobble is exactly what someone
        choosing a take is choosing between."""
        cached = self._cel_thumbs.get((cel.id, take))
        if cached is not None and cached[0] == cel.version:
            return cached[1]
        thumb = cairo.ImageSurface(cairo.FORMAT_ARGB32, THUMB_W, THUMB_H)
        ctx = cairo.Context(thumb)
        ctx.set_source_rgb(1, 1, 1)
        ctx.paint()
        fx, fy, fw, fh = self._thumb_frame(cel)
        scale = min(THUMB_W / fw, THUMB_H / fh)
        ctx.translate((THUMB_W - fw * scale) / 2., (THUMB_H - fh * scale) / 2.)
        ctx.scale(scale, scale)
        ctx.translate(-fx, -fy)
        try:
            ctx.set_source_surface(cel.decoded(take), 0, 0)
            # magnified, a drawing shows its own pixels — this is a pixel app
            ctx.get_source().set_filter(cairo.FILTER_NEAREST if scale >= 1
                                        else cairo.FILTER_GOOD)
            ctx.paint()
        except Exception:
            pass
        self._cel_thumbs[(cel.id, take)] = (cel.version, thumb)
        return thumb

    def _cel_row_selected(self, _list, row):
        self._library_cel = row.cel_id if (row is not None and
                                           hasattr(row, 'cel_id')) else None
        self._refresh_takes()

    def _takes_cel(self):
        chosen = getattr(self, '_library_cel', None)
        if chosen is not None:
            cel = self.doc.cel(chosen)
            if cel is not None:
                return cel
        return self._active_cel()

    def _refresh_takes(self):
        """The takes strip: the chosen drawing's variants as numbered
        buttons, the active one held down — plus add-a-copy and remove.
        This is how a boil is drawn by hand: add a take, rough over it."""
        for child in self.takes_box.get_children():
            self.takes_box.remove(child)
        cel = self._takes_cel()
        if cel is None:
            return
        active = min(self.active_take.get(cel.id, 0), len(cel.takes) - 1)
        for index in range(len(cel.takes)):
            button = Gtk.ToggleButton(label=str(index + 1))
            button.set_active(index == active)
            button.set_tooltip_text(_t('Choose Take'))
            button.get_accessible().set_name(_t('Choose Take'))

            def _pick(b, cel_id=cel.id, i=index):
                if not b.get_active():
                    b.set_active(True)
                    return
                self.active_take[cel_id] = i
                self._refresh_takes()
                self.canvas.queue_draw()

            button.connect('clicked', _pick)
            self.takes_box.pack_start(button, False, False, 0)
        add = Gtk.Button(label='+')
        add.set_tooltip_text(_t('Add Take'))
        add.get_accessible().set_name(_t('Add Take'))
        add.set_sensitive(len(cel.takes) < TAKE_MAX)
        add.connect('clicked', self._add_take)
        self.takes_box.pack_start(add, False, False, 4)
        remove = Gtk.Button(label='−')
        remove.set_tooltip_text(_t('Remove Take'))
        remove.get_accessible().set_name(_t('Remove Take'))
        remove.set_sensitive(len(cel.takes) > 1)
        remove.connect('clicked', self._remove_take)
        self.takes_box.pack_start(remove, False, False, 0)
        self.takes_box.show_all()

    def _add_take(self, *_):
        cel = self._takes_cel()
        if cel is None or len(cel.takes) >= TAKE_MAX:
            return
        self._snapshot(_t('Add Take'))
        source = cel.decoded(self.active_take.get(cel.id, 0))
        copy_surface = surface(cel.w, cel.h)
        ctx = cairo.Context(copy_surface)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_surface(source)
        ctx.paint()
        cel.takes.append(copy_surface)
        self.active_take[cel.id] = len(cel.takes) - 1
        cel.version += 1
        self._commit_change()

    def _remove_take(self, *_):
        cel = self._takes_cel()
        if cel is None or len(cel.takes) <= 1:
            return
        self._snapshot(_t('Remove Take'))
        index = min(self.active_take.get(cel.id, 0), len(cel.takes) - 1)
        cel.takes.pop(index)
        self.active_take[cel.id] = max(0, index - 1)
        cel.version += 1
        self._commit_change()

    def _cel_in_use(self, cel_id):
        return any(run['cel'] == cel_id
                   for scene in self.doc.scenes
                   for layer in scene['layers']
                   for run in layer['runs'])

    def _delete_cel(self, *_):
        cel_id = getattr(self, '_library_cel', None)
        if cel_id is None or self._cel_in_use(cel_id):
            return
        cel = self.doc.cel(cel_id)
        if cel is None:
            return
        self._snapshot(_t('Delete Drawing'))
        self.doc.cels.remove(cel)
        self._library_cel = None
        self._commit_change()

    def _cel_row_activated(self, _list, row):
        if hasattr(row, 'cel_id'):
            self._rename_cel_prompt(cel_id=row.cel_id)

    def _cel_list_press(self, _widget, event):
        """Press-and-hold a drawing's row to see it on the canvas; release
        puts the frame back. A look costs nothing and changes nothing."""
        row = self.cel_list.get_row_at_y(int(event.y))
        if row is not None and hasattr(row, 'cel_id'):
            self._preview_cel = row.cel_id
            self.canvas.queue_draw()
        return False

    def _cel_list_release(self, _widget, _event):
        if getattr(self, '_preview_cel', None) is not None:
            self._preview_cel = None
            self.canvas.queue_draw()
        return False

    def _refresh_lists(self):
        signature = tuple((cel.id, cel.version, cel.name)
                          for cel in self.doc.cels)
        if signature != getattr(self, '_cel_list_signature', None):
            self._cel_list_signature = signature
            self._sync_cel_rows()
        self._refresh_layers()
        self._refresh_project_palette()
        self._refresh_takes()

    def _build_cel_hint(self):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        hint = Gtk.Label(label=_t('The animation has no drawings. Drawing on the canvas makes one.'),
                         xalign=0)
        hint.set_line_wrap(True)
        # The first sentence someone reads in this app ran into the
        # right edge of the screen: the row gave it the panel's
        # whole width and every drawing row beside it has margins.
        hint.set_margin_start(4)
        hint.set_margin_end(10)
        hint.set_margin_top(4)
        hint.set_margin_bottom(4)
        hint.get_style_context().add_class('animation-muted')
        row.add(hint)
        return row

    def _draw_cel_thumb(self, area, cr, cel_id):
        """Paint a library row's picture, once the row is actually on screen.

        Building the picture as the row was built meant opening a film cost
        one PNG decode and one ink-bounds scan per drawing, for pictures
        nobody had scrolled to yet: 1.12 of the 1.24 seconds it took to open
        a four-hundred-drawing film, and it climbed with the library. GTK
        only draws what is visible, so asking here spends that on the dozen
        rows a person can see, and _cel_thumb_surface keeps each one after.

        By id, never by holding the cel: undo re-parses the film, and a
        drawing captured in this closure would be one from a dead document.
        """
        cel = self.doc.cel(cel_id)
        if cel is None:
            return False
        thumb = self._cel_thumb_surface(cel)
        left = round((area.get_allocated_width() - THUMB_W) / 2.)
        top = round((area.get_allocated_height() - THUMB_H) / 2.)
        cr.set_source_surface(thumb, left, top)
        cr.paint()
        # Each picture is its own object. Blank paper is white and the rows
        # sit flush, so seven drawings read as ONE white column in a list
        # whose whole point is navigating by picture rather than by name.
        # The Choose Take picker already frames its thumbnails this way.
        # Half-pixel offsets, or a hairline covers neither pixel and draws
        # nothing at all.
        cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
        cr.set_line_width(1)
        cr.rectangle(left + .5, top + .5, THUMB_W - 1, THUMB_H - 1)
        cr.stroke()
        return False

    def _build_cel_row(self, cel):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=8)
        picture = Gtk.DrawingArea()
        picture.set_size_request(THUMB_W, THUMB_H)
        picture.connect('draw', self._draw_cel_thumb, cel.id)
        box.pack_start(picture, False, False, 2)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = Gtk.Label(xalign=0)
        nbi18n.set_verbatim(name, cel.name)      # the film's words again
        name.set_ellipsize(Pango.EllipsizeMode.END)
        words.pack_start(name, False, False, 0)
        takes = Gtk.Label(label=(_t('%d take%s') %
                                 (len(cel.takes),
                                  's' if len(cel.takes) != 1 else '')),
                          xalign=0)
        takes.get_style_context().add_class('animation-muted')
        words.pack_start(takes, False, False, 0)
        box.pack_start(words, True, True, 0)
        row.add(box)
        row.cel_id = cel.id
        target = Gtk.TargetEntry.new('application/x-animation-cel',
                                     Gtk.TargetFlags.SAME_APP, 0)
        row.connect('drag-begin',
                    lambda *_a: self._cel_list_release(None, None))
        row.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [target],
                            Gdk.DragAction.COPY)
        row.connect('drag-data-get', self._cel_drag_data_get, cel.id)
        row.connect('drag-begin', self._cel_drag_begin, cel)
        return row

    def _sync_cel_rows(self):
        """Bring the library list into line with the film, row by row.

        Emptying the list and rebuilding every row cost 92ms at 385
        drawings and climbs with the library — on the path that runs when
        someone makes a NEW drawing, which is the commonest action in the
        app, and Article VIII B2 allows 50ms in a callback. Touch only what
        actually changed: appending a drawing should cost one row.
        """
        rows = getattr(self, '_cel_rows', None)
        if rows is None:
            rows = self._cel_rows = {}
        if not self.doc.cels:
            for cel_id, (row, _stamp) in list(rows.items()):
                self.cel_list.remove(row)
                del rows[cel_id]
            if getattr(self, '_cel_hint_row', None) is None:
                self._cel_hint_row = self._build_cel_hint()
                self.cel_list.add(self._cel_hint_row)
                self._cel_hint_row.show_all()
            return
        if getattr(self, '_cel_hint_row', None) is not None:
            self.cel_list.remove(self._cel_hint_row)
            self._cel_hint_row = None
        alive = {cel.id for cel in self.doc.cels}
        for cel_id, (row, _stamp) in list(rows.items()):
            if cel_id not in alive:
                self.cel_list.remove(row)
                del rows[cel_id]
        for index, cel in enumerate(self.doc.cels):
            stamp = (cel.version, cel.name, len(cel.takes))
            entry = rows.get(cel.id)
            if entry is not None and entry[1] == stamp:
                if entry[0].get_index() != index:
                    self.cel_list.remove(entry[0])
                    self.cel_list.insert(entry[0], index)
                continue
            if entry is not None:
                self.cel_list.remove(entry[0])
            row = self._build_cel_row(cel)
            self.cel_list.insert(row, index)
            row.show_all()
            rows[cel.id] = (row, stamp)

    def _cel_drag_data_get(self, _row, _context, selection, _info,
                           _time, cel_id):
        """Put the private cel identity into a library-row drag."""
        selection.set_text(str(cel_id), -1)

    def _cel_drag_begin(self, _row, context, cel):
        """Carry the library's real cel thumbnail under the pointer."""
        Gtk.drag_set_icon_surface(context, self._cel_thumb_surface(cel))

    def _timeline_drop_target(self, x, y):
        """Map a drop through the exposure sheet's shared row geometry."""
        scene = self.doc.scenes[self.scene_i]
        if x < TL_GUTTER or y < TL_ROWS_TOP:
            return None
        row = int((y - TL_ROWS_TOP) // TL_ROW_H)
        if not 0 <= row < len(scene['layers']):
            return None
        frame = self.view_origin + int((x - TL_GUTTER) // self.column_width)
        if not 0 <= frame < scene['length']:
            return None
        return (len(scene['layers']) - row - 1, frame)

    def _timeline_drag_motion(self, _widget, context, x, y, time):
        """Advertise COPY only over an actual exposure-sheet cell."""
        if self._timeline_drop_target(x, y) is None:
            Gdk.drag_status(context, Gdk.DragAction.DEFAULT, time)
        else:
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def _timeline_drag_data_received(self, _widget, context, x, y,
                                     selection, _info, time):
        """Stamp a dragged library cel to the next exposure or scene end."""
        target = self._timeline_drop_target(x, y)
        try:
            cel_id = int(selection.get_text())
        except (TypeError, ValueError):
            Gtk.drag_finish(context, False, False, time)
            return
        cel = self.doc.cel(cel_id)
        if target is None or cel is None:
            Gtk.drag_finish(context, False, False, time)
            return
        layer_index, frame = target
        scene = self.doc.scenes[self.scene_i]
        runs = scene['layers'][layer_index]['runs']
        next_start = min((run['start'] for run in runs
                          if run['start'] > frame), default=scene['length'])
        self._snapshot(_t('New Drawing'))
        try:
            self.sheet.stamp(layer_index,
                             make_run(cel_id, frame, next_start - frame))
        except ValueError:
            self._undo.pop()
            self._flash(_t('The exposures would overlap or run past the scene.'))
            Gtk.drag_finish(context, False, False, time)
            return
        self.layer_i = layer_index
        self.playhead = frame
        self.selection = (layer_index, frame, next_start)
        self._commit_change()
        Gtk.drag_finish(context, True, False, time)

    def _refresh_layers(self):
        for child in self.layer_list.get_children():
            self.layer_list.remove(child)
        layers = self.doc.scenes[self.scene_i]['layers']
        active_row = None
        for index in reversed(range(len(layers))):
            layer = layers[index]
            row = Gtk.ListBoxRow()
            box = Gtk.Box()
            eye = Gtk.CheckButton()
            eye.set_tooltip_text(_t('Layer Visibility'))
            eye.set_active(layer.get('visible', True))
            eye.connect('toggled', self._toggle_layer, index)
            box.pack_start(eye, False, False, 2)
            name = Gtk.Label(xalign=0)
            # The film's own words, not the app's: a layer called "Room"
            # came out as ルーム in Japanese because the catalog happens to
            # hold that word. Nothing the person named may be translated.
            nbi18n.set_verbatim(name, layer['name'])
            name.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(name, True, True, 2)
            if layer.get('mouth_slots'):
                mouth = Gtk.Label(label='M')
                mouth.set_tooltip_text(_t('Mouth Slots'))
                box.pack_start(mouth, False, False, 2)
            row.add(box)
            row.layer_index = index
            self.layer_list.add(row)
            if index == self.layer_i:
                active_row = row
        self.layer_list.show_all()
        if active_row is not None:
            self.layer_list.select_row(active_row)
        self._refresh_layer_buttons()

    def _refresh_layer_buttons(self):
        """The dock's four layer buttons say what the menu says.

        The Layer menu greys these commands at their limits; the buttons
        beside the list did not, so at six layers the + stayed lit, clicking
        it did nothing and said nothing. The takes buttons above them were
        already right, which is how the dock came to disagree with itself.
        Spec: at every cap the creating control disables, with its reason in
        the tooltip."""
        buttons = getattr(self, '_layer_buttons', None)
        if not buttons:
            return
        layers = self.doc.scenes[self.scene_i]['layers']
        allowed = {
            'New Layer': (len(layers) < LAYER_MAX,
                          _t('This scene holds as many layers as it can.')),
            'Delete Layer': (len(layers) > 1,
                             _t('A scene keeps at least one layer.')),
            'Move Layer Up': (self.layer_i < len(layers) - 1,
                              _t('This layer is already at the top.')),
            'Move Layer Down': (self.layer_i > 0,
                                _t('This layer is already at the bottom.')),
        }
        for name, (usable, because) in allowed.items():
            button = buttons.get(name)
            if button is None:
                continue
            button.set_sensitive(usable)
            button.set_tooltip_text(_t(name) if usable else because)

    def _layer_row_selected(self, _list, row):
        """Clicking a layer row makes it the drawing target — the selected
        row IS the statement of where the next stroke lands."""
        if row is None or not hasattr(row, 'layer_index'):
            return
        if self.layer_i != row.layer_index:
            self.layer_i = row.layer_index
            self.timeline.queue_draw()

    def _toggle_layer(self, button, index):
        self._snapshot(_t('Layer Visibility'))
        self.doc.scenes[self.scene_i]['layers'][index]['visible'] = button.get_active()
        self._commit_change()

    def _add_layer(self, *_):
        layers = self.doc.scenes[self.scene_i]['layers']
        if len(layers) >= LAYER_MAX:
            return
        self._snapshot(_t('New Layer'))
        layers.append(new_layer(_t('Layer %d') % (len(layers) + 1)))
        self.layer_i = len(layers) - 1
        self._commit_change()

    def _delete_layer(self, *_):
        layers = self.doc.scenes[self.scene_i]['layers']
        if len(layers) <= 1:
            return
        self._snapshot(_t('Delete Layer'))
        layers.pop(self.layer_i)
        self.layer_i = min(self.layer_i, len(layers) - 1)
        self._commit_change()

    def _raise_layer(self, *_):
        self._move_layer(1)

    def _lower_layer(self, *_):
        self._move_layer(-1)

    def _move_layer(self, delta):
        layers = self.doc.scenes[self.scene_i]['layers']
        target = self.layer_i + delta
        if not 0 <= target < len(layers):
            return
        self._snapshot(_t('Move Layer'))
        layers[self.layer_i], layers[target] = layers[target], layers[self.layer_i]
        self.layer_i = target
        self._commit_change()

    def _commit_change(self):
        self._cache.clear()
        stale = self._scene_thumbs.pop(self.scene_i, None)
        if stale is not None:
            self._scene_thumb_stale = (self.scene_i, stale)
        self._scene_thumb_dirty = (self.scene_i, time.monotonic())
        self._mark_dirty()
        self._refresh_lists()
        self.canvas.queue_draw()
        self.timeline.queue_draw()

    def _clear_scene_thumbs(self):
        """Forget indexed scene chrome after structural scene changes."""
        self._scene_thumbs.clear()
        self._scene_thumb_dirty = None
        self._scene_thumb_stale = None

    def menu_items(self, name):
        if name == 'File':
            return [('New…    Ctrl+N', self._new), ('Open…    Ctrl+O', self._open),
                    nbapp.SEP, ('Save    Ctrl+S', self._save),
                    ('Save As…    Ctrl+Shift+S', self._save_as), nbapp.SEP,
                    ('Export Movie…', self._export), nbapp.SEP,
                    ('Close    Esc', self.close)]
        if name == 'Edit':
            return nbapp.undo_menu_items(self.history) + [
                nbapp.SEP,
                ('Cut    Ctrl+X', self._cut_selection if self.selection else None),
                ('Copy    Ctrl+C', self._copy_selection if self.selection else None),
                ('Paste    Ctrl+V', self._paste_selection if self.sheet.clipboard else None),
                nbapp.SEP,
                ('Copy Frame as Image', self._copy_frame_image),
            ]
        if name == 'View':
            onion = ('Onion Skin: Off', 'Onion Skin: One Drawing',
                     'Onion Skin: Two Drawings')[self.onion]
            return [
                (onion + '    Ctrl+E', self._cycle_onion),
                (self._ticked(self.grid, _t('Pixel Grid    G')),
                 self._toggle_grid),
                nbapp.SEP,
                ('Zoom In    Ctrl+Plus', lambda: self._zoom_step(1)),
                ('Zoom Out    Ctrl+Minus', lambda: self._zoom_step(-1)),
                ('Fit    Ctrl+0', self._fit_canvas),
            ]
        if name == 'Paint':
            # The dock is 887px of controls in a 406px column at the design
            # size, and 284px at 1024x600 — so tip, pattern, mirror and the
            # project palette all sat below the fold with no other way to
            # reach them. Every other thing this app can do has a menu.
            # Menus set the dock's own controls rather than the state behind
            # them, so the two can never come to disagree.
            def press(button, wanted=None):
                return lambda: button.set_active(
                    (not button.get_active()) if wanted is None else wanted)

            tip = getattr(self, '_shape_buttons', {})
            fill = getattr(self, '_pattern_buttons', {})
            flip = getattr(self, '_mirror_buttons', {})
            lock = getattr(self, '_palette_lock_check', None)
            return [
                (self._ticked(self.shape == 'square', _t('Square Tip')),
                 press(tip['square'], True) if 'square' in tip else None),
                (self._ticked(self.shape == 'round', _t('Round Tip')),
                 press(tip['round'], True) if 'round' in tip else None),
                nbapp.SEP,
                (self._ticked(self.pattern == PATTERNS[0], _t('Solid Colour')),
                 press(fill[PATTERNS[0]], True) if PATTERNS[0] in fill else None),
                (self._ticked(self.pattern == PATTERNS[1], _t('Checker')),
                 press(fill[PATTERNS[1]], True) if PATTERNS[1] in fill else None),
                (self._ticked(self.pattern == PATTERNS[2], _t('Sparse')),
                 press(fill[PATTERNS[2]], True) if PATTERNS[2] in fill else None),
                nbapp.SEP,
                (self._ticked(self.symx, _t('Mirror Left and Right')),
                 press(flip['symx']) if 'symx' in flip else None),
                (self._ticked(self.symy, _t('Mirror Top and Bottom')),
                 press(flip['symy']) if 'symy' in flip else None),
                nbapp.SEP,
                ('    ' + _t('Add Palette Colour'),
                 self._palette_add
                 if (self.color not in self.doc.palette
                     and len(self.doc.palette) < 16) else None),
                ('    ' + _t('Remove Palette Colour'),
                 self._palette_remove if self.doc.palette else None),
                (self._ticked(self.doc.palette_only, _t('Draw with Palette Only')),
                 press(lock) if lock is not None else None),
            ]
        if name == 'Timeline':
            return [
                ('New Drawing    N',
                 self._new_drawing if len(self.doc.cels) < CEL_MAX else None),
                ('Duplicate Drawing    D',
                 self._duplicate_drawing
                 if len(self.doc.cels) < CEL_MAX else None),
                nbapp.SEP,
                ('Extend Hold    =', self._extend_hold),
                ('Shorten Hold    -', self._shorten_hold),
                ('Split Hold    /', self._split_hold),
                ('Clear Exposure    Delete',
                 self._clear_exposure if self.selection else None),
                nbapp.SEP,
                ('Repeat Selection…    Ctrl+R',
                 self._repeat_prompt
                 if (self.selection or self.sheet.clipboard) else None),
                ('Slide Between Exposures',
                 self._slide_selection if self.selection else None),
                ('Insert Frames…', self._insert_prompt),
                ('Remove Frames…', self._remove_prompt),
                nbapp.SEP,
                (('Stop    Space' if self._playing else 'Play    Space'),
                 self._toggle_playback),
                (self._ticked(self.loop, _t('Loop')), self._toggle_loop),
                ('Stamp Mouths', self._toggle_stamp_mouths),
                nbapp.SEP,
                ('Previous Frame    ,', self._step_back),
                ('Next Frame    .', self._step_forward),
                ('First Frame    Home', self._go_start),
                ('Last Frame    End', self._go_end),
                nbapp.SEP,
                ('Add Marker…    M', self._marker_prompt),
            ]
        if name == 'Scene':
            return [
                ('New Scene',
                 self._new_scene
                 if (len(self.doc.scenes) < SCENE_MAX and
                     self._room_for(self.doc.fps * 8)) else None),
                ('Duplicate Scene',
                 self._duplicate_scene
                 if (len(self.doc.scenes) < SCENE_MAX and
                     self._room_for(self.doc.scenes[self.scene_i]['length']))
                 else None),
                ('Delete Scene', self._delete_scene if len(self.doc.scenes) > 1 else None),
                nbapp.SEP,
                ('Previous Scene    Page Up',
                 (lambda: self._switch_scene(self.scene_i - 1))
                 if self.scene_i else None),
                ('Next Scene    Page Down',
                 (lambda: self._switch_scene(self.scene_i + 1))
                 if self.scene_i + 1 < len(self.doc.scenes) else None),
                nbapp.SEP,
                ('Move Scene Left', (lambda: self._move_scene(-1)) if self.scene_i else None),
                ('Move Scene Right', (lambda: self._move_scene(1)) if self.scene_i + 1 < len(self.doc.scenes) else None),
                ('Rename Scene…', self._rename_scene_prompt),
                ('Scene Length…', self._scene_length_prompt),
            ]
        if name == 'Drawing':
            return [
                ('Rename Drawing…',
                 self._rename_cel_prompt if self._active_cel() else None),
                ('Delete Drawing',
                 self._delete_cel
                 if (getattr(self, '_library_cel', None) is not None and
                     not self._cel_in_use(self._library_cel)) else None),
                ('Choose Take…',
                 self._choose_take_prompt if self._active_cel() else None),
                ('Add Take',
                 self._add_take
                 if (self._takes_cel() is not None and
                     len(self._takes_cel().takes) < TAKE_MAX) else None),
                ('Remove Take',
                 self._remove_take
                 if (self._takes_cel() is not None and
                     len(self._takes_cel().takes) > 1) else None),
                ('Add Wobble Takes…',
                 self._wobble_prompt if self._active_cel() else None),
                ('Recolor Drawing to Palette',
                 self._recolor_cel
                 if (self.doc.palette and self._active_cel()) else None),
                ('Place Image…', self._place_image),
            ]
        if name == 'Layer':
            layers = self.doc.scenes[self.scene_i]['layers']
            return [('New Layer',
                     self._add_layer if len(layers) < LAYER_MAX else None),
                    ('Delete Layer',
                     self._delete_layer if len(layers) > 1 else None),
                    ('Move Layer Up',
                     self._raise_layer
                     if self.layer_i < len(layers) - 1 else None),
                    ('Move Layer Down',
                     self._lower_layer if self.layer_i > 0 else None),
                    nbapp.SEP,
                    ('Rename Layer…', self._rename_layer_prompt),
                    ('Mouth Slots…', self._mouth_slots_prompt),
                    ('Mouth from Loudness…', self._mouth_loudness_prompt)]
        if name == 'Sound':
            return [('Add Sound…', self._add_sound),
                    ('Record Sound…', self._record_prompt),
                    ('Remove Sound', self._remove_sound if self._selected_sound else None)]
        return []

    def _canvas_point(self, event):
        allocation = self.canvas.get_allocation()
        width, height = self.doc.canvas
        scale = self.zoom
        left = (allocation.width - width * scale) / 2
        top = (allocation.height - height * scale) / 2
        return (int((event.x - left) // scale), int((event.y - top) // scale))

    def _canvas_footprint_runs(self):
        """Return the exact row runs the next canvas stamp will write.

        This follows Illustrator's brush-footprint mechanism: the chrome and
        byte writer share ``brush_runs``, so the pointer cannot promise pixels
        different from those the tool changes.
        """
        if self.tool in ('select', 'picker'):
            return ()
        shape = 'round' if self.tool == 'brush' else self.shape
        return brush_runs(self.size, shape)

    def _canvas_footprint_rect(self, point=None):
        """Return a widget-coordinate damage box for a footprint."""
        point = getattr(self, '_canvas_cursor', None) if point is None else point
        runs = self._canvas_footprint_runs()
        if point is None or not runs:
            return None
        dx0 = min(run[1] for run in runs)
        dx1 = max(run[2] for run in runs)
        dy0 = min(run[0] for run in runs)
        dy1 = max(run[0] for run in runs)
        allocation = self.canvas.get_allocation()
        width, height = self.doc.canvas
        left = (allocation.width - width * self.zoom) / 2
        top = (allocation.height - height * self.zoom) / 2
        x = left + (point[0] + dx0) * self.zoom
        y = top + (point[1] + dy0) * self.zoom
        w = (dx1 - dx0 + 1) * self.zoom
        h = (dy1 - dy0 + 1) * self.zoom
        return (math.floor(x) - 2, math.floor(y) - 2,
                math.ceil(w) + 5, math.ceil(h) + 5)

    def _damage_canvas_footprints(self, old_point=None):
        """Invalidate only the old and new pointer chrome rectangles."""
        for rect in (self._canvas_footprint_rect(old_point),
                     self._canvas_footprint_rect()):
            if rect is not None:
                self.canvas.queue_draw_area(*rect)

    @staticmethod
    def _brush_outline(runs):
        """Trace Illustrator's row-convex brush boundary as one polygon."""
        rows = sorted(runs)
        if not rows:
            return ()
        if any(b[0] != a[0] + 1 for a, b in zip(rows, rows[1:])):
            return ()
        left = []
        right = []
        for dy, dx0, _dx1 in rows:
            left.extend(((dx0, dy), (dx0, dy + 1)))
        for dy, _dx0, dx1 in reversed(rows):
            right.extend(((dx1 + 1, dy + 1), (dx1 + 1, dy)))
        return tuple(left) + tuple(right)

    def _draw_canvas_footprint(self, cr, scale):
        """Paint a hard signage-red outline around the next stamp."""
        point = getattr(self, '_canvas_cursor', None)
        runs = self._canvas_footprint_runs()
        if point is None or not runs:
            return
        outline = self._brush_outline(runs)
        if not outline:
            return
        cr.save()
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        x0, y0 = outline[0]
        half = 0.5 / scale
        cr.move_to(point[0] + x0 + half, point[1] + y0 + half)
        for x, y in outline[1:]:
            cr.line_to(point[0] + x + half, point[1] + y + half)
        cr.close_path()
        cr.set_line_width(1 / scale)
        cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
        cr.stroke()
        cr.restore()

    def _opaque_bounds(self, cel, take):
        """Cache the alpha bounds of a cel take by identity and version.

        The scan reads each row as one byte slice and lets lstrip/rstrip
        find its edges: a 320x240 cel is 76,800 Python steps walked pixel
        by pixel, and every drawing in the library now needs this to frame
        its thumbnail."""
        cache = getattr(self, '_opaque_bounds_cache', None)
        if cache is None:
            cache = self._opaque_bounds_cache = {}
        key = (cel.id, cel.version, take)
        if key in cache:
            return cache[key]
        surface_take = cel.decoded(take)
        surface_take.flush()
        data = surface_take.get_data()
        stride = surface_take.get_stride()
        left = cel.w
        top = cel.h
        right = -1
        bottom = -1
        for y in range(cel.h):
            row = y * stride
            alpha = bytes(data[row + 3:row + cel.w * 4:4])
            lit = alpha.lstrip(b'\x00')
            if not lit:
                continue
            top = min(top, y)
            bottom = y
            left = min(left, len(alpha) - len(lit))
            right = max(right, len(alpha.rstrip(b'\x00')) - 1)
        bounds = None if right < left else (left, top,
                                             right - left + 1,
                                             bottom - top + 1)
        cache[key] = bounds
        return bounds

    def _selected_canvas_bounds(self):
        """Resolve the selected run's current-take opaque bounds."""
        if not self.selection or self.selection[0] != self.layer_i:
            return None
        layer_index, start, end = self.selection
        layer = self.doc.scenes[self.scene_i]['layers'][layer_index]
        frame = min(max(self.playhead, start), end - 1)
        run = run_at(layer['runs'], frame)
        if run is None:
            return None
        cel = self.doc.cel(run['cel'])
        if cel is None:
            return None
        take = take_index(run, frame, len(cel.takes), self.doc.boil_every)
        bounds = self._opaque_bounds(cel, take)
        if bounds is None:
            return None
        return (bounds[0] + run.get('dx', 0),
                bounds[1] + run.get('dy', 0), bounds[2], bounds[3])

    def _sync_canvas_cursor(self):
        """Apply Illustrator's canvas cursor vocabulary to the GDK window."""
        window = self.canvas.get_window()
        if window is None:
            return
        # Illustrator uses a crosshair over its pixel canvas. Animation keeps
        # the system arrow for Select and uses that crosshair for every tool
        # that addresses a canvas pixel, including the eyedropper.
        name = 'default' if self.tool == 'select' else 'crosshair'
        try:
            window.set_cursor(Gdk.Cursor.new_from_name(
                self.canvas.get_display(), name))
        except Exception:
            window.set_cursor(None)

    def _ensure_canvas_pointer_events(self):
        """Install enter/leave tracking without widening the dock/build edit."""
        if getattr(self, '_canvas_pointer_events_ready', False):
            return
        self._canvas_pointer_events_ready = True
        self.canvas.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK |
                               Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.canvas.connect('enter-notify-event', self._canvas_enter)
        self.canvas.connect('leave-notify-event', self._canvas_leave)

    def _canvas_enter(self, _widget, event):
        old = getattr(self, '_canvas_cursor', None)
        point = self._canvas_point(event)
        if 0 <= point[0] < self.doc.canvas[0] and \
                0 <= point[1] < self.doc.canvas[1]:
            self._canvas_cursor = point
        self._sync_canvas_cursor()
        self._damage_canvas_footprints(old)
        return False

    def _canvas_leave(self, _widget, _event):
        old = getattr(self, '_canvas_cursor', None)
        self._canvas_cursor = None
        self._damage_canvas_footprints(old)
        window = self.canvas.get_window()
        if window is not None:
            window.set_cursor(None)
        return False

    def _canvas_press(self, _widget, event):
        self._ensure_canvas_pointer_events()
        self.canvas.grab_focus()
        self._sync_canvas_cursor()
        point = self._canvas_point(event)
        if not (0 <= point[0] < self.doc.canvas[0] and
                0 <= point[1] < self.doc.canvas[1]):
            return False
        if self.tool == 'select':
            self._select_at_pixel(point)
            if self.selection:
                self._snapshot(_t('Move Exposure'))
                self._drag_anchor = point
            return True
        if self.tool == 'picker':
            frame = composite(self.doc, self.doc.scenes[self.scene_i], self.playhead)
            pixel = pix_at(frame, *point)
            self._choose_colour(None, '#%02X%02X%02X' %
                                (pixel[2], pixel[1], pixel[0]))
            self.tool = self.previous_tool
            return True
        self._snapshot(_t('Draw'))
        cel, run = self.sheet.ensure_drawing(self.layer_i, self.playhead)
        if cel is None:
            # The library is full, so this frame cannot have a drawing.
            # Saying nothing here is a pencil that leaves no ink.
            self._undo.pop()
            self._flash(_t('This film holds as many drawings as it can.'))
            return True
        self._edit_cel = cel
        self._edit_take = self.active_take.get(cel.id, 0)
        self._drawing = True
        self._draw_anchor = point
        self._draw_last = point
        self._paint_point(point)
        self._flash(_t("Drawing on '%s' - %d frames show this.") %
                    (cel.name, run['len']))
        return True

    def _canvas_motion(self, _widget, event):
        point = self._canvas_point(event)
        old_cursor = getattr(self, '_canvas_cursor', None)
        inside = (0 <= point[0] < self.doc.canvas[0] and
                  0 <= point[1] < self.doc.canvas[1])
        self._canvas_cursor = point if inside else None
        self._sync_canvas_cursor()
        self._damage_canvas_footprints(old_cursor)
        if getattr(self, '_drawing', False):
            for x, y in _line_points(self._draw_last[0], self._draw_last[1],
                                     point[0], point[1]):
                self._paint_point((x, y))
            self._draw_last = point
            self.canvas.queue_draw()
            return True
        if self.tool == 'select' and self.selection and hasattr(self, '_drag_anchor'):
            dx = point[0] - self._drag_anchor[0]
            dy = point[1] - self._drag_anchor[1]
            run = run_at(self.doc.scenes[self.scene_i]['layers'][self.layer_i]['runs'],
                         self.playhead)
            if run:
                run['dx'] += dx
                run['dy'] += dy
                self._drag_anchor = point
                self._cache.clear()
                self.canvas.queue_draw()
            return True
        return False

    def _canvas_release(self, _widget, _event):
        self._sync_canvas_cursor()
        if getattr(self, '_drawing', False):
            self._drawing = False
            self._edit_cel.version += 1
            self._commit_change()
            return True
        if hasattr(self, '_drag_anchor'):
            del self._drag_anchor
            self._mark_dirty()
            return True
        return False

    def _paint_point(self, point):
        cel = self._edit_cel
        take = cel.decoded(self._edit_take)
        erase = self.tool == 'eraser'
        value = CLEAR4 if erase else px4(self.color)
        pattern = self.pattern if self.tool in ('pencil', 'brush', 'fill') else 'solid'
        shape = 'round' if self.tool == 'brush' else self.shape
        stamp(take, point[0], point[1], self.size, shape, value, pattern,
              self.symx, self.symy)

    def _select_at_pixel(self, point):
        scene = self.doc.scenes[self.scene_i]
        for index in reversed(range(len(scene['layers']))):
            layer = scene['layers'][index]
            if not layer.get('visible', True):
                continue
            run = run_at(layer['runs'], self.playhead)
            if not run:
                continue
            cel = self.doc.cel(run['cel'])
            take = take_index(run, self.playhead, len(cel.takes), self.doc.boil_every)
            pixel = pix_at(cel.decoded(take), point[0] - run['dx'], point[1] - run['dy'])
            if pixel[3]:
                self.layer_i = index
                self.selection = (index, run['start'], run['start'] + run['len'])
                self._refresh_layers()
                return
        self.selection = None

    def _frame_to_x(self, frame):
        """Sheet frame -> timeline x, through the one scroll origin."""
        return TL_GUTTER + (frame - self.view_origin) * self.column_width

    def _x_to_frame(self, x):
        """Timeline x -> sheet frame, clamped at zero."""
        return max(0, self.view_origin +
                   int((x - TL_GUTTER) // self.column_width))

    def _follow_playhead(self):
        """Keep the playhead inside the visible window of the sheet."""
        width = self.timeline.get_allocated_width()
        visible = max(1, (width - TL_GUTTER) // self.column_width)
        if self.playhead < self.view_origin:
            self.view_origin = self.playhead
        elif self.playhead >= self.view_origin + visible - 2:
            # never past the frame being followed: before the sheet has a
            # real width `visible` is 1, and the old arithmetic scrolled to
            # frame 1 while the playhead sat on frame 0
            self.view_origin = max(0, min(self.playhead,
                                          self.playhead - visible + 2))

    def _edge_scroll(self, x):
        """Dragging near either edge walks the view along the sheet."""
        width = self.timeline.get_allocated_width()
        if x < TL_GUTTER + 10:
            self.view_origin = max(0, self.view_origin - 2)
        elif x > width - 12:
            scene = self.doc.scenes[self.scene_i]
            visible = max(1, (width - TL_GUTTER) // self.column_width)
            self.view_origin = max(0, min(scene['length'] - visible // 2,
                                          self.view_origin + 2))

    def _timeline_tooltip(self, _widget, x, y, _keyboard, tip):
        """Drawn controls still introduce themselves: the transport and the
        scene cards answer hover the way real buttons would."""
        if y >= TL_STRIP_H:
            return False
        names = {'prev': _t('Previous frame'), 'next': _t('Next frame'),
                 'loop': _t('Loop'), 'onion': _t('Onion Skin'),
                 'mouths': _t('Stamp Mouths'),
                 'playstop': _t('Stop') if self._playing else _t('Play')}
        for left, right, action in getattr(self, '_transport', []):
            if left <= x <= right:
                tip.set_text(names.get(action, ''))
                return True
        for left, right, index in getattr(self, '_scene_cards', []):
            if left <= x <= right:
                tip.set_text(_t('New Scene') if index == 'add'
                             else self.doc.scenes[index]['name'])
                return True
        return False

    def _timeline_press(self, _widget, event):
        self.timeline.grab_focus()
        if event.y < TL_STRIP_H:
            for left, right, action in getattr(self, '_transport', []):
                if left <= event.x <= right:
                    if action == 'mouths':
                        self._toggle_stamp_mouths()
                    elif action == 'playstop':
                        if self._playing:
                            self._stop_playback()
                        else:
                            self._start_playback()
                    elif action == 'prev':
                        self.playhead = max(0, self.playhead - 1)
                        self._update_playhead()
                        self._scrub_frame()
                    elif action == 'next':
                        scene = self.doc.scenes[self.scene_i]
                        self.playhead = min(scene['length'] - 1,
                                            self.playhead + 1)
                        self._update_playhead()
                        self._scrub_frame()
                    elif action == 'loop':
                        self.loop = not self.loop
                        self.timeline.queue_draw()
                    elif action == 'onion':
                        self._cycle_onion()
                        self.timeline.queue_draw()
                    return True
            for left, right, index in getattr(self, '_scene_cards', []):
                if left <= event.x <= right:
                    if index == 'add':
                        self._new_scene()
                    else:
                        # switching happens on release; a held press begins
                        # a reorder drag along the strip
                        self._scene_card_drag = {'index': index,
                                                 'moved': False}
                    return True
            return True
        scene = self.doc.scenes[self.scene_i]
        band = getattr(self, '_extent_band', None)
        if band and band[0] <= event.y <= band[1] and event.x >= TL_GUTTER:
            width_all = self.timeline.get_allocated_width()
            band_w = max(1, width_all - TL_GUTTER - 8)
            fraction = min(1.0, max(0.0, (event.x - TL_GUTTER) / band_w))
            self.playhead = int(fraction * (scene['length'] - 1))
            self._ruler_drag = True
            self._update_playhead()
            self._scrub_frame()
            return True
        if TL_STRIP_H <= event.y < TL_ROWS_TOP:
            for marker in scene['markers']:
                marker_x = self._frame_to_x(marker['frame'])
                if abs(event.x - marker_x) <= 6:
                    self.playhead = marker['frame']
                    self._update_playhead()
                    if event.type == Gdk.EventType._2BUTTON_PRESS:
                        self._marker_prompt()
                    return True
            for left, right, delta in getattr(self, '_ruler_stepper', []):
                if left <= event.x <= right:
                    if delta:
                        widths = (3, 6, 12, 24)
                        index = widths.index(self.column_width) \
                            if self.column_width in widths else 1
                        self.column_width = widths[index + delta]
                        self._follow_playhead()
                        self.timeline.queue_draw()
                    return True
        if event.x < TL_GUTTER and event.y >= TL_ROWS_TOP:
            # the gutter is the row's name: clicking it chooses the row
            row = int((event.y - TL_ROWS_TOP) // TL_ROW_H)
            if 0 <= row < len(scene['layers']):
                self.layer_i = len(scene['layers']) - row - 1
                self._refresh_layers()
                self.timeline.queue_draw()
            return True
        frame = self._x_to_frame(event.x)
        self.playhead = min(scene['length'] - 1, frame)
        if event.y < TL_ROWS_TOP:
            # dragging along the ruler scrubs; the press already lands here
            self._ruler_drag = True
        row = int((event.y - TL_ROWS_TOP) // TL_ROW_H)
        if 0 <= row < len(scene['layers']):
            shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
            hit_layer = len(scene['layers']) - row - 1
            if shift and self.selection:
                # a rectangle of sheet: from the anchor to here, across
                # frames AND layers (spec §5's block selection)
                anchor_layer, anchor_start, anchor_end = self.selection
                lo = min(anchor_layer, hit_layer)
                hi = max(anchor_layer, hit_layer)
                start = min(anchor_start, frame)
                end = max(anchor_end, frame + 1)
                self.selection = (anchor_layer, start, end)
                self.selection_layers = (lo, hi)
                self._selected_sound = None
                self._refresh_layers()
                self._update_playhead()
                return True
            self.layer_i = hit_layer
            run = run_at(scene['layers'][self.layer_i]['runs'], self.playhead)
            if run:
                self.selection = (self.layer_i, run['start'],
                                  run['start'] + run['len'])
                self.selection_layers = (self.layer_i, self.layer_i)
            else:
                self.selection_layers = None
                # a held press on the bar begins a move-in-time drag; the
                # undo frame is taken lazily, on the first real movement
                self._run_drag = {'layer': self.layer_i, 'run': run,
                                  'anchor': frame, 'origin': run['start'],
                                  'moved': False}
            self._selected_sound = None
            self._refresh_layers()
        sound_row = int((event.y - (TL_ROWS_TOP + LAYER_MAX * TL_ROW_H)) // TL_ROW_H)
        if 0 <= sound_row < SOUND_ROWS:
            sound = scene['sounds'][sound_row]
            if sound:
                start = sound['start']
                duration = max(0, sound.get('duration_smp', 0) -
                               sound.get('in_smp', 0) - sound.get('out_smp', 0))
                frames = max(1, math.ceil(duration / SPF[self.doc.fps]))
                if start <= frame < start + frames:
                    self._selected_sound = (self.scene_i, sound_row)
                    self.selection = None
                    dot_x = self._frame_to_x(start + frames) - 8
                    if abs(event.x - dot_x) <= 8:
                        self._snapshot(_t('Mute Sound'))
                        sound['mute'] = not sound.get('mute', False)
                        self._commit_change()
                        return True
                    self._snapshot(_t('Move Sound'))
                    edge = 6 / max(1, self.column_width)
                    if frame - start <= edge:
                        mode = 'trim-in'
                    elif start + frames - frame <= edge:
                        mode = 'trim-out'
                    else:
                        mode = 'move'
                    self._sound_drag = (sound_row, mode, frame,
                                        copy.deepcopy(sound))
        self._update_playhead()
        self._scrub_frame()
        return True

    def _timeline_motion(self, _widget, event):
        card_drag = getattr(self, '_scene_card_drag', None)
        if card_drag is not None:
            target = None
            for left, right, index in getattr(self, '_scene_cards', []):
                if index != 'add' and left <= event.x <= right:
                    target = index
                    break
            if target is not None and target != card_drag['index']:
                if not card_drag['moved']:
                    self._snapshot(_t('Move Scene'))
                    card_drag['moved'] = True
                scenes = self.doc.scenes
                scene = scenes.pop(card_drag['index'])
                scenes.insert(target, scene)
                if self.scene_i == card_drag['index']:
                    self.scene_i = target
                elif card_drag['index'] < self.scene_i <= target:
                    self.scene_i -= 1
                elif target <= self.scene_i < card_drag['index']:
                    self.scene_i += 1
                card_drag['index'] = target
                self._clear_scene_thumbs()
                self.sheet = Sheet(self.doc, self.scene_i)
                self.timeline.queue_draw()
            return True
        if getattr(self, '_ruler_drag', False):
            scene = self.doc.scenes[self.scene_i]
            frame = min(scene['length'] - 1, self._x_to_frame(event.x))
            self._edge_scroll(event.x)
            if frame != self.playhead:
                self.playhead = frame
                self._update_playhead()
                self._scrub_frame()
            return True
        drag = getattr(self, '_run_drag', None)
        if drag is not None:
            scene = self.doc.scenes[self.scene_i]
            frame = self._x_to_frame(event.x)
            self._edge_scroll(event.x)
            target = max(0, min(scene['length'] - drag['run']['len'],
                                drag['origin'] + frame - drag['anchor']))
            run = drag['run']
            if target != run['start']:
                others = [r for r in scene['layers'][drag['layer']]['runs']
                          if r is not run]
                clear = all(r['start'] + r['len'] <= target or
                            r['start'] >= target + run['len'] for r in others)
                if clear:
                    if not drag['moved']:
                        # one undo frame per gesture, taken before the first
                        # movement lands (the snapshot-before-mutation law)
                        keep = run['start']
                        run['start'] = drag['origin']
                        self._snapshot(_t('Move Exposure'))
                        run['start'] = keep
                        drag['moved'] = True
                    run['start'] = target
                    self.selection = (drag['layer'], target,
                                      target + run['len'])
                    self.timeline.queue_draw()
            return True
        if not hasattr(self, '_sound_drag'):
            return False
        row, mode, anchor, before = self._sound_drag
        sound = self.doc.scenes[self.scene_i]['sounds'][row]
        frame = self._x_to_frame(event.x)
        self._edge_scroll(event.x)
        delta = frame - anchor
        spf = SPF[self.doc.fps]
        if mode == 'move':
            sound['start'] = max(0, min(self.doc.scenes[self.scene_i]['length'] - 1,
                                        before['start'] + delta))
        elif mode == 'trim-in':
            maximum = max(0, before.get('duration_smp', 0) -
                          before.get('out_smp', 0) - spf)
            sound['in_smp'] = max(0, min(maximum,
                                         before.get('in_smp', 0) + delta * spf))
        else:
            maximum = max(0, before.get('duration_smp', 0) -
                          before.get('in_smp', 0) - spf)
            sound['out_smp'] = max(0, min(maximum,
                                          before.get('out_smp', 0) - delta * spf))
        self.timeline.queue_draw()
        return True

    def _timeline_release(self, _widget, _event):
        handled = False
        card_drag = getattr(self, '_scene_card_drag', None)
        if card_drag is not None:
            if card_drag['moved']:
                self._commit_change()
            else:
                self._switch_scene(card_drag['index'])
            self._scene_card_drag = None
            handled = True
        if getattr(self, '_ruler_drag', False):
            self._ruler_drag = False
            handled = True
        drag = getattr(self, '_run_drag', None)
        if drag is not None:
            if drag['moved']:
                scene = self.doc.scenes[self.scene_i]
                scene['layers'][drag['layer']]['runs'].sort(
                    key=lambda r: r['start'])
                self._commit_change()
            self._run_drag = None
            handled = True
        if hasattr(self, '_sound_drag'):
            row, _mode, _anchor, before = self._sound_drag
            del self._sound_drag
            now = self.doc.scenes[self.scene_i]['sounds'][row]
            if now == before:
                # Only a click, to select the sound. The snapshot was taken
                # on press before anyone knew that, and keeping it left a
                # "Move Sound" step that undoes nothing and a film marked
                # unsaved over an edit that never happened — so the close
                # guard asked about it and the first Ctrl+Z did nothing.
                if self._undo:
                    self._undo.pop()
            else:
                self._mark_dirty()
            handled = True
        return handled

    def _remove_sound(self, *_):
        if not self._selected_sound:
            return
        scene_index, row = self._selected_sound
        self._snapshot(_t('Remove Sound'))
        self.doc.scenes[scene_index]['sounds'][row] = None
        self._selected_sound = None
        self._commit_change()

    def _toggle_stamp_mouths(self):
        if self.stamp_mouths:
            self.stamp_mouths = False
            self._mouth_pass_open = False
            self._mark_dirty()
        else:
            slots = self.doc.scenes[self.scene_i]['layers'][self.layer_i].get('mouth_slots')
            if not slots:
                self._flash(_t('Assign mouth slots to the active layer first.'))
                return
            self._snapshot(_t('Stamp Mouths'))
            self.stamp_mouths = True
            self.loop = True
            self._mouth_pass_open = True
        self.timeline.queue_draw()

    def _stamp_mouth(self, slot_number):
        layer = self.doc.scenes[self.scene_i]['layers'][self.layer_i]
        slots = layer.get('mouth_slots') or []
        if not slots:
            self._flash(_t('Assign mouth slots to the active layer first.'))
            return
        index = 0 if slot_number == 0 else slot_number - 1
        if index >= len(slots):
            self._flash(_t('That mouth slot is empty.'))
            return
        self.sheet.clear(self.layer_i, self.playhead, self.playhead + 1)
        self.sheet.stamp(self.layer_i,
                         make_run(slots[index], self.playhead, 1),
                         replace=True)
        self._cache.clear()
        # Deliberately no snapshot per stamp — one covers the whole pass,
        # taken when the mode opens, because a snapshot serialises the
        # entire film. But the film HAS changed, and until now nothing said
        # so: close in the middle of a pass and the guard let a whole
        # lip-sync take go without asking.
        self._mark_dirty()
        self.canvas.queue_draw()
        self.timeline.queue_draw()

    def _update_playhead(self, targeted=False):
        """Move the playhead and repaint what actually changed.

        B5 asks an animation to invalidate its own allocation and no more.
        A playing film moves one red line, one readout and one dot: with
        `targeted` the sheet repaints those strips instead of all of it,
        which is the difference between 6% and 60% of a frame budget on
        the software renderer this OS is measured against. Anything that
        can change the sheet's CONTENT leaves targeted False.
        """
        scene = self.doc.scenes[self.scene_i]
        self.playhead = max(0, min(scene['length'] - 1, self.playhead))
        previous_origin = self.view_origin
        previous_x = getattr(self, '_playhead_x', None)
        self._follow_playhead()
        seconds, frame = divmod(self.playhead, self.doc.fps)
        minutes, seconds = divmod(seconds, 60)
        # position AND the scene's length: a film-maker's first question is
        # "how long is this?", and the answer was previously only inside the
        # export card. Digits and a slash, so no string needs translating.
        total = max(0, scene['length'] - 1)
        end_seconds, end_frame = divmod(total, self.doc.fps)
        end_minutes, end_seconds = divmod(end_seconds, 60)
        self.readout.set_text('%d:%02d+%02d / %d:%02d+%02d'
                              % (minutes, seconds, frame,
                                 end_minutes, end_seconds, end_frame))
        self.scene_status.set_text((_t('%d fps') % self.doc.fps) + '   ' +
                                   (_t('Scene %d of %d') %
                                    (self.scene_i + 1, len(self.doc.scenes))))
        self.canvas.queue_draw()
        moved_view = self.view_origin != previous_origin
        current_x = self._frame_to_x(self.playhead)
        if targeted and not moved_view and previous_x is not None:
            height = TL_ROWS_TOP + (LAYER_MAX + 2) * TL_ROW_H + 12
            for x in (previous_x, current_x):
                self.timeline.queue_draw_area(int(x) - 6, 0, 18, height)
            width = self.timeline.get_allocated_width()
            # the transport readout, and the extent band's whole row. The
            # rect must cover where the readout STARTS: it carries
            # position / length now, so it reaches further left than the
            # first version of this optimisation assumed.
            self.timeline.queue_draw_area(width - 470, 0, 330, TL_STRIP_H)
            self.timeline.queue_draw_area(
                0, TL_ROWS_TOP + (LAYER_MAX + 2) * TL_ROW_H, width, 10)
        else:
            self.timeline.queue_draw()
        self._playhead_x = current_x

    def _new_drawing(self, *_):
        if len(self.doc.cels) >= CEL_MAX:
            self._flash(_t('This film holds as many drawings as it can.'))
            return
        self._snapshot(_t('New Drawing'))
        self.sheet.ensure_drawing(self.layer_i, self.playhead,
                                  duplicate=False, force_new=True)
        self._commit_change()

    def _duplicate_drawing(self, *_):
        if len(self.doc.cels) >= CEL_MAX:
            self._flash(_t('This film holds as many drawings as it can.'))
            return
        self._snapshot(_t('Duplicate Drawing'))
        self.sheet.ensure_drawing(self.layer_i, self.playhead, True)
        self._commit_change()

    def _extend_hold(self, *_):
        self._snapshot(_t('Extend Hold'))
        if self.sheet.extend(self.layer_i, self.playhead):
            self._commit_change()
        else:
            self._undo.pop()

    def _shorten_hold(self, *_):
        self._snapshot(_t('Shorten Hold'))
        if self.sheet.shorten(self.layer_i, self.playhead):
            self._commit_change()
        else:
            self._undo.pop()

    def _split_hold(self, *_):
        self._snapshot(_t('Split Hold'))
        if self.sheet.split(self.layer_i, self.playhead):
            self._commit_change()
        else:
            self._undo.pop()

    def _clear_exposure(self, *_):
        if not self.selection:
            return
        _layer, start, end = self.selection
        self._snapshot(_t('Clear Exposure'))
        for index in self._selected_layers():
            self.sheet.clear(index, start, end)
        self.selection = None
        self.selection_layers = None
        self._commit_change()

    def _selected_layers(self):
        """Every layer the block covers — one, unless Shift widened it."""
        if not self.selection:
            return []
        if self.selection_layers:
            lo, hi = self.selection_layers
            return list(range(lo, hi + 1))
        return [self.selection[0]]

    def _copy_selection(self, *_):
        if self.selection:
            _layer, start, end = self.selection
            self.sheet.copy_block(self._selected_layers(), start, end)

    def _cut_selection(self, *_):
        self._copy_selection()
        self._clear_exposure()

    def _paste_refusal(self):
        """Why a paste could not happen, in the person's terms."""
        clipboard = self.sheet.clipboard
        if not clipboard or not clipboard[1]:
            return _t('Select whole exposures to repeat.')
        return _t('The exposures would overlap or run past the scene.')

    def _paste_selection(self, *_):
        self._snapshot(_t('Paste Exposures'))
        if self.sheet.paste(self.playhead):
            self._commit_change()
        else:
            self._undo.pop()
            self._flash(self._paste_refusal())

    def _overlay_prompt(self, title, rows, apply_label, callback, note=None):
        """Open the shared in-window card and mirror inputs into plain state.

        This follows Illustrator's overlay-prompt/state-dict mechanism: widget
        lifetimes never leak into apply callbacks, and Escape only cancels.
        """
        if self._prompt_layer is not None:
            return
        state = {spec[0]: spec[2] for spec in rows}
        self._prompt_previews = []
        allocation = self._overlay.get_allocation()
        screen_width, screen_height = nbapp.screen_size()
        width = allocation.width if allocation.width > 1 else screen_width
        height = allocation.height if allocation.height > 1 else screen_height
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.set_size_request(width, height)
        scrim.connect('button-press-event',
                      lambda *_: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class('animation-prompt')
        card.pack_start(Gtk.Label(label=_t(title), xalign=0), False, False, 0)
        # One column for the names so the controls beside them line up. The
        # New Animation card has two rows of choices and they started at
        # different places, which is the first card anyone opens.
        names = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        # A row may say when it applies: ('other key', (values,), why not).
        # The export card offers a video size and a GIF size at once, and
        # both stayed live whichever kind was chosen — so picking GIF size
        # 3x for a video export did nothing and said nothing.
        self._prompt_when = []
        for spec in rows:
            key, label, initial, kind = spec[:4]
            when = spec[4] if len(spec) > 4 else None
            row = Gtk.Box(spacing=8)
            # a row whose control wants width gets it: the name of the
            # control does not need to grow, and splitting the row evenly
            # is what truncated the take names and shrank the sliders
            head = kind[0] if isinstance(kind, tuple) else kind
            expand = head in ('slots-picker', 'float', 'mouth-preview',
                              'meter', 'take-picker')
            name = Gtk.Label(label=_t(label), xalign=0)
            # beside a tall control the name is a heading, not a floating word
            name.set_valign(Gtk.Align.START)
            names.add_widget(name)
            row.pack_start(name, False, False, 0)
            if head == 'int':
                low, high = ((kind[1], kind[2]) if isinstance(kind, tuple)
                             else (1, SCENE_FRAME_MAX))
                widget = Gtk.SpinButton.new_with_range(low, max(low, high), 1)
                widget.set_value(initial)
                widget.connect('value-changed',
                               lambda item, k=key: state.__setitem__(k, item.get_value_as_int()))
            elif head == 'float':
                # A control must not offer a value its own apply refuses:
                # a threshold dragged past what a sound can reach, or a
                # strength the clamp silently pulls back, reads as a dead
                # stretch of slider that does nothing.
                low, high = (kind[1], kind[2]) if isinstance(kind, tuple) else (0., 2.)
                widget = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                  low, high, .01)
                # a slider you cannot drag is not a control: without a width
                # the prompt's row packing collapsed these to a stub, and the
                # thresholds are the whole point of the loudness card
                widget.set_size_request(260, -1)
                widget.set_value(initial)
                widget.connect('value-changed', self._prompt_float_changed,
                               state, key)
            elif kind == 'check':
                widget = Gtk.CheckButton()
                widget.set_active(initial)
                widget.connect('toggled',
                               lambda item, k=key: state.__setitem__(k, item.get_active()))
            elif kind == 'slots-picker':
                # Every drawing as a clickable picture. Clicking adds the
                # drawing as the NEXT mouth slot (its slot number badges the
                # thumb); clicking a chosen one removes it. The order of
                # clicks IS the slot order — no numbers to type.
                widget = Gtk.FlowBox()
                widget.set_selection_mode(Gtk.SelectionMode.NONE)
                widget.set_min_children_per_line(3)
                widget.set_max_children_per_line(6)
                widget.set_size_request(320, -1)
                for cel in self.doc.cels:
                    tile = Gtk.Button()
                    tile.set_relief(Gtk.ReliefStyle.NONE)
                    tile.get_style_context().add_class('animation-slot-tile')
                    stack = Gtk.Overlay()
                    stack.add(Gtk.Image.new_from_surface(
                        self._cel_thumb_surface(cel)))
                    badge = Gtk.Label()
                    badge.set_halign(Gtk.Align.START)
                    badge.set_valign(Gtk.Align.START)
                    badge.get_style_context().add_class('animation-slot-badge')
                    stack.add_overlay(badge)
                    tile.add(stack)
                    tile.set_tooltip_text(cel.name)
                    tile.get_accessible().set_name(cel.name)

                    # Framing the ink makes the mouths legible but drops
                    # where each one sat on the sheet, and two characters'
                    # mouths draw alike. Say the name of whatever the
                    # pointer or the focus ring is on.
                    def _tile_name(_w, _e=None, name=cel.name):
                        note_label = getattr(self, '_prompt_note', None)
                        if note_label is not None:
                            note_label.set_text(name)
                        return False

                    def _tile_unname(_w, _e=None):
                        note_label = getattr(self, '_prompt_note', None)
                        if note_label is not None:
                            note_label.set_text(_t(note) if note else '')
                        return False

                    tile.connect('enter-notify-event', _tile_name)
                    tile.connect('leave-notify-event', _tile_unname)
                    tile.connect('focus-in-event', _tile_name)
                    tile.connect('focus-out-event', _tile_unname)

                    def _tile_toggle(_b, cel_id=cel.id, badge=badge,
                                     k=key, st=state):
                        chosen = list(st[k])
                        if cel_id in chosen:
                            chosen.remove(cel_id)
                        elif len(chosen) < 8:
                            chosen.append(cel_id)
                        st[k] = chosen
                        for child, lbl in getattr(self, '_prompt_badges', []):
                            ident = getattr(child, '_slot_cel', None)
                            numbered = ident in chosen
                            lbl.set_text(str(chosen.index(ident) + 1)
                                         if numbered else '')
                            # an empty badge is a mystery box on every tile
                            lbl.set_visible(numbered)

                    tile._slot_cel = cel.id
                    if not hasattr(self, '_prompt_badges'):
                        self._prompt_badges = []
                    self._prompt_badges.append((tile, badge))
                    numbered = cel.id in state[key]
                    badge.set_text(str(state[key].index(cel.id) + 1)
                                   if numbered else '')
                    badge.set_no_show_all(not numbered)
                    badge.set_visible(numbered)
                    tile.connect('clicked', _tile_toggle)
                    widget.add(tile)
            elif kind == 'take-picker':
                # Choosing a take is choosing between pictures, so show the
                # pictures. Cycling belongs in the list: it is what a run
                # does by default, and a card that could only name fixed
                # takes left a run that had been fixed unable to cycle again.
                widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                widget.set_size_request(300, -1)
                cel = self._active_cel()
                first = None
                for value in range(0, (len(cel.takes) if cel else 1) + 1):
                    option = Gtk.RadioButton.new_from_widget(first)
                    if first is None:
                        first = option
                    option.set_mode(False)
                    option.get_style_context().add_class('animation-take-option')
                    line = Gtk.Box()
                    if cel is not None:
                        line.pack_start(Gtk.Image.new_from_surface(
                            self._take_thumb_surface(cel, max(0, value - 1))),
                            False, False, 4)
                    words = Gtk.Label(
                        label=(_t('Every take in turn') if not value
                               else _t('Take %d') % value), xalign=0)
                    words.set_ellipsize(Pango.EllipsizeMode.END)
                    line.pack_start(words, True, True, 4)
                    option.add(line)
                    option.set_active(value == initial)
                    option.connect(
                        'toggled',
                        lambda item, k=key, v=value: (
                            state.__setitem__(k, v) if item.get_active()
                            else None))
                    widget.pack_start(option, False, False, 0)
            elif kind == 'meter':
                widget = Gtk.ProgressBar()
                widget.set_size_request(180, 18)
                self._record_meter = widget
            elif isinstance(kind, tuple) and kind[0] == 'choices':
                widget = Gtk.Box(spacing=4)
                first = None
                for value, text in kind[1]:
                    choice = Gtk.RadioButton.new_with_label_from_widget(first, text)
                    if first is None:
                        first = choice
                    choice.set_active(value == initial)
                    choice.connect('toggled', self._prompt_choice, state,
                                   key, value)
                    widget.pack_start(choice, False, False, 0)
            elif kind == 'mouth-preview':
                widget = Gtk.DrawingArea()
                widget.set_size_request(300, 72)
                widget.connect('draw', self._draw_mouth_preview, state)
                self._prompt_previews.append(widget)
            elif kind == 'wobble-preview':
                widget = Gtk.DrawingArea()
                widget.set_size_request(120, 90)
                widget._wobble_surface = None
                widget.connect('draw', self._draw_wobble_preview)
                self._prompt_previews.append(widget)
            else:
                widget = Gtk.Entry(text=str(initial))
                widget.connect('changed',
                               lambda item, k=key: state.__setitem__(k, item.get_text()))
            row.pack_start(widget, expand, expand, 0)
            card.pack_start(row, False, False, 0)
            if when:
                self._prompt_when.append((row, when))
        self._apply_prompt_when(state)
        self._prompt_note = None
        if note:
            quiet = Gtk.Label(label=_t(note), xalign=0)
            # The note carries real sentences AND, in the slot picker, the
            # name of whatever is under the pointer. Wrapping to a bounded
            # two lines fits the sentences without letting a long drawing
            # name change the height of the card under someone's hand.
            quiet.set_line_wrap(True)
            quiet.set_max_width_chars(46)
            quiet.set_lines(2)
            quiet.set_ellipsize(Pango.EllipsizeMode.END)
            quiet.get_style_context().add_class('animation-muted')
            card.pack_start(quiet, False, False, 0)
            self._prompt_note = quiet
        actions = Gtk.Box(homogeneous=True)
        cancel = Gtk.Button(label=_t('Cancel'))
        accept = Gtk.Button(label=_t(apply_label))
        cancel.connect('clicked', lambda *_: self._close_prompt())
        accept.connect('clicked', lambda *_: self._apply_prompt(callback, state))
        actions.pack_start(cancel, True, True, 0)
        actions.pack_start(accept, True, True, 0)
        card.pack_start(actions, False, False, 0)
        card_window = Gtk.EventBox()
        card_window.add(card)
        layer.put(card_window, 0, 0)
        self._overlay.add_overlay(layer)
        self._prompt_layer = layer
        self._prompt_callback = callback
        self._prompt_state = state
        layer.show_all()
        _minimum, natural = card_window.get_preferred_size()
        card_width = natural.width if natural.width > 1 else 420
        card_height = natural.height if natural.height > 1 else 220
        layer.move(card_window, max(0, (width - card_width) // 2),
                   max(0, (height - card_height) // 2))
        for preview in self._prompt_previews:
            if hasattr(preview, '_wobble_surface'):
                self._schedule_wobble_preview(state)

    def _prompt_choice(self, button, state, key, value):
        if button.get_active():
            state[key] = value
            self._apply_prompt_when(state)

    def _apply_prompt_when(self, state):
        """Rows that do not apply go quiet, and say why.

        Disabled, never absent: the row stays where it was so the card does
        not change shape under the pointer, and it carries the reason rather
        than simply refusing to respond.
        """
        for row, (other, values, because) in getattr(self, '_prompt_when', ()):
            usable = state.get(other) in values
            row.set_sensitive(usable)
            row.set_tooltip_text(None if usable else _t(because))

    def _prompt_float_changed(self, widget, state, key):
        state[key] = widget.get_value()
        for preview in self._prompt_previews:
            preview.queue_draw()
            if hasattr(preview, '_wobble_surface'):
                self._schedule_wobble_preview(state)

    def _schedule_wobble_preview(self, state):
        """Debounce the one generated take owned by the open Wobble card."""
        source = getattr(self, '_prompt_preview_timer', 0)
        if source:
            GLib.source_remove(source)
        self._prompt_preview_timer = GLib.timeout_add(
            200, self._refresh_wobble_preview, state)

    def _refresh_wobble_preview(self, state):
        """Generate one deterministic chrome-only wobble preview take."""
        self._prompt_preview_timer = 0
        if self._prompt_layer is None:
            return False
        cel = self._active_cel()
        if cel is None:
            return False
        strength = min(1.8, max(.7, state.get('strength', 1.1)))
        preview = wobble_take(cel.decoded(0), cel.id, 2, strength)
        for widget in self._prompt_previews:
            if hasattr(widget, '_wobble_surface'):
                widget._wobble_surface = preview
                widget.queue_draw()
        return False

    def _draw_wobble_preview(self, widget, context):
        """Overlay take zero and one wobbled take in the Wobble card."""
        cel = self._active_cel()
        if cel is None:
            return False
        context.set_antialias(cairo.ANTIALIAS_NONE)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        scale = min(width / cel.w, height / cel.h)
        left = (width - cel.w * scale) / 2
        top = (height - cel.h * scale) / 2
        context.save()
        context.translate(left, top)
        context.scale(scale, scale)
        context.set_source_surface(cel.decoded(0), 0, 0)
        context.get_source().set_filter(cairo.FILTER_BILINEAR)
        context.paint()
        preview = widget._wobble_surface
        if preview is not None:
            context.set_source_surface(preview, 0, 0)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint_with_alpha(.5)
        context.restore()
        return False

    def _draw_mouth_preview(self, widget, context, state):
        context.set_antialias(cairo.ANTIALIAS_NONE)
        slots = self._mouth_preview_slots(state.get('quiet', .10),
                                          state.get('loud', .45))
        width = max(1, widget.get_allocated_width())
        cell = width / max(1, len(slots))
        colours = ((154 / 255, 148 / 255, 132 / 255),
                   (127 / 255, 169 / 255, 140 / 255),
                   (200 / 255, 52 / 255, 30 / 255))
        for index, slot in enumerate(slots):
            context.set_source_rgb(*colours[min(2, slot - 1)])
            context.rectangle(index * cell, 0, math.ceil(cell), 28)
            context.fill()
        layer = self.doc.scenes[self.scene_i]['layers'][self.layer_i]
        mouth_slots = layer.get('mouth_slots') or []
        for index, cel_id in enumerate(mouth_slots[:3]):
            cel = self.doc.cel(cel_id)
            if cel is None:
                continue
            thumb = self._cel_thumb_surface(cel)
            x = index * 52
            context.set_source_surface(thumb, x, 34)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint()
            context.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            context.rectangle(x + .5, 34.5, 44, 33)
            context.stroke()
        return False

    def _apply_prompt(self, callback, state):
        snapshot = copy.deepcopy(state)
        self._close_prompt()
        callback(snapshot)

    def _close_prompt(self):
        self._prompt_badges = []
        source = getattr(self, '_prompt_preview_timer', 0)
        if source:
            GLib.source_remove(source)
            self._prompt_preview_timer = 0
        if self._prompt_layer is None:
            return
        self._overlay.remove(self._prompt_layer)
        self._prompt_layer = None
        self._prompt_callback = None
        self._prompt_state = None

    def _repeat_prompt(self, *_):
        if not self.selection and not self.sheet.clipboard:
            # The card used to open on nothing and then blame the scene
            # for being too short, sending someone to look for room that
            # was never the problem.
            self._flash(_t('Select the exposures to repeat first.'))
            return
        self._overlay_prompt('Repeat Selection…',
                             [('count', 'Copies', 2, 'int')],
                             'Repeat', self._repeat_apply,
                             'The copies start at the current frame.')

    def _repeat_apply(self, state):
        if not self.sheet.clipboard and self.selection:
            self._copy_selection()
        self._snapshot(_t('Repeat Selection'))
        if self.sheet.paste(self.playhead, max(1, state['count'])):
            self._commit_change()
        else:
            self._undo.pop()
            self._flash(self._paste_refusal())

    def _insert_prompt(self, *_):
        room = max(1, PROJECT_FRAME_MAX - self._project_frames())
        self._overlay_prompt('Insert Frames…',
                             [('count', 'Frames', 1, ('int', 1, room))],
                             'Insert', self._insert_apply,
                             'Sounds stay where they are.')

    def _insert_apply(self, state):
        if not self._room_for(state['count']):
            self._flash(_t('The film is at its full length.'))
            return
        self._snapshot(_t('Insert Frames'))
        if self.sheet.insert(self.playhead, state['count']):
            self._commit_change()
        else:
            self._undo.pop()

    def _remove_prompt(self, *_):
        scene = self.doc.scenes[self.scene_i]
        here = max(1, scene['length'] - self.playhead)
        self._overlay_prompt('Remove Frames…',
                             [('count', 'Frames', 1, ('int', 1, here))],
                             'Remove', self._remove_apply,
                             'Sounds stay where they are.')

    def _remove_apply(self, state):
        self._snapshot(_t('Remove Frames'))
        self.sheet.remove(self.playhead, state['count'])
        self._commit_change()

    def _slide_selection(self, *_):
        if not self.selection:
            return
        layer, start, end = self.selection
        self._snapshot(_t('Slide Between Exposures'))
        # A selection's end is EXCLUSIVE everywhere in this app, but slide
        # looks up a run AT the frame it is given. Handing it `end` asked
        # about the frame just past the second exposure — so selecting two
        # exposures and sliding between them refused every time.
        if self.sheet.slide(layer, start, max(start, end - 1)):
            self._commit_change()
        else:
            # Four different reasons end up here — the ends are not two
            # exposures, they are the same one, they hold different
            # drawings, or something already fills the gap — and every one
            # of them used to look exactly like the command not working.
            self._undo.pop()
            self._flash(_t('Select two exposures of the same drawing with '
                           'space between them.'))

    def _marker_prompt(self, *_):
        markers = self.doc.scenes[self.scene_i]['markers']
        existing = next((m for m in markers if m['frame'] == self.playhead),
                        None)
        # The card names the operation it is about to do: opened on a
        # frame that already carries a marker it renames one, and saying
        # "Add" there described something else entirely.
        self._overlay_prompt('Rename Marker…' if existing else 'Add Marker…',
                             [('text', 'Name',
                               existing['text'] if existing else '', 'text')],
                             'Rename' if existing else 'Add',
                             self._marker_apply,
                             'An empty name removes the marker.' if existing
                             else 'The marker names the current frame.')

    def _marker_apply(self, state):
        markers = self.doc.scenes[self.scene_i]['markers']
        old = next((m for m in markers if m['frame'] == self.playhead), None)
        text = state['text'].strip()
        if old and not text:
            self._snapshot(_t('Remove Marker'))
            markers.remove(old)
        elif old:
            self._snapshot(_t('Add Marker'))
            old['text'] = text
        else:
            # a nameless marker is a beat flag; adding one is still an add
            self._snapshot(_t('Add Marker'))
            markers.append({'frame': self.playhead, 'text': text})
            markers.sort(key=lambda marker: marker['frame'])
        self._commit_change()

    def _toggle_playback(self, *_):
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _toggle_loop(self, *_):
        self.loop = not self.loop
        self.timeline.queue_draw()

    def _step_back(self, *_):
        self.playhead = max(0, self.playhead - 1)
        self._update_playhead(targeted=True)
        self._scrub_frame()

    def _step_forward(self, *_):
        scene = self.doc.scenes[self.scene_i]
        self.playhead = min(scene['length'] - 1, self.playhead + 1)
        self._update_playhead(targeted=True)
        self._scrub_frame()

    def _go_start(self, *_):
        self.playhead = 0
        self._update_playhead()

    def _go_end(self, *_):
        self.playhead = self.doc.scenes[self.scene_i]['length'] - 1
        self._update_playhead()

    def _project_frames(self):
        """Every frame the film currently holds, across all its scenes."""
        return sum(scene['length'] for scene in self.doc.scenes)

    def _room_for(self, frames):
        """Whether the film can grow by this many frames and stay inside
        PROJECT_FRAME_MAX — the cap the spec set so a project stays inside
        the memory of the machine it was made for."""
        return self._project_frames() + frames <= PROJECT_FRAME_MAX

    def _new_scene(self, *_):
        if len(self.doc.scenes) >= SCENE_MAX:
            return
        if not self._room_for(self.doc.fps * 8):
            self._flash(_t('The film is at its full length.'))
            return
        self._snapshot(_t('New Scene'))
        self.doc.scenes.insert(self.scene_i + 1,
                               new_scene(_t('Scene %d') % (len(self.doc.scenes) + 1), fps=self.doc.fps))
        self._clear_scene_thumbs()
        self._switch_scene(self.scene_i + 1)
        self._commit_change()

    def _duplicate_scene(self, *_):
        if len(self.doc.scenes) >= SCENE_MAX:
            return
        if not self._room_for(self.doc.scenes[self.scene_i]['length']):
            self._flash(_t('The film is at its full length.'))
            return
        self._snapshot(_t('Duplicate Scene'))
        duplicate = copy.deepcopy(self.doc.scenes[self.scene_i])
        duplicate['name'] = _t('%s copy') % duplicate['name']
        self.doc.scenes.insert(self.scene_i + 1, duplicate)
        self._clear_scene_thumbs()
        self._switch_scene(self.scene_i + 1)
        self._commit_change()

    def _delete_scene(self, *_):
        if len(self.doc.scenes) <= 1:
            return
        self._snapshot(_t('Delete Scene'))
        self.doc.scenes.pop(self.scene_i)
        self._clear_scene_thumbs()
        self._switch_scene(min(self.scene_i, len(self.doc.scenes) - 1))
        self._commit_change()

    def _move_scene(self, delta):
        target = self.scene_i + delta
        if not 0 <= target < len(self.doc.scenes):
            return
        self._snapshot(_t('Move Scene'))
        scenes = self.doc.scenes
        scenes[self.scene_i], scenes[target] = scenes[target], scenes[self.scene_i]
        self._clear_scene_thumbs()
        # Moving the scene you are standing in changes where it sits in the
        # film, not what is inside it. Switching scenes rightly starts you
        # at the beginning of a DIFFERENT scene; here it threw away the
        # frame you were on and the exposures you had selected, every time
        # you nudged a scene along.
        where = (self.playhead, self.layer_i, self.selection,
                 self.selection_layers, self.view_origin)
        self._switch_scene(target)
        (self.playhead, self.layer_i, self.selection,
         self.selection_layers, self.view_origin) = where
        self._update_playhead()
        self._commit_change()

    def _cels_used_by(self, scene_index):
        """The cel ids a scene's exposures actually show."""
        if not 0 <= scene_index < len(self.doc.scenes):
            return set()
        return {run['cel']
                for layer in self.doc.scenes[scene_index]['layers']
                for run in layer['runs']}

    def _compact_cels(self):
        """Ask for the working set to be put back to bytes, in idle time.

        Spec §1's memory model: only the ACTIVE scene and the one before it
        keep decoded surfaces; everything else holds its PNG bytes, which is
        also the save encoding. Two promises land here — a 320x240 take
        costs 300 KB decoded, and an autosave re-encodes only what is still
        decoded (measured: 87 ms to 15 ms on a 288-cel film, 84 MB to 1 MB).

        The work happens in an IDLE handler, a couple of takes at a time,
        because encoding one take costs about 4 ms and doing a film's worth
        inside the switch put a 59 ms hitch in a move that should feel
        instant. Encoding rather than caching-by-version is the safe
        choice: bytes cannot go stale, while a version cache would serve
        old pixels the day an edit path forgets to bump.
        """
        if getattr(self, '_compact_source', 0):
            return
        self._compact_source = GLib.idle_add(self._compact_step)

    def _compact_step(self):
        if not self._alive:
            self._compact_source = 0
            return False
        keep = self._cels_used_by(self.scene_i)
        keep |= self._cels_used_by(getattr(self, '_previous_scene', -1))
        budget = 2
        for cel in self.doc.cels:
            if cel.id in keep:
                continue
            for index, take in enumerate(cel.takes):
                if isinstance(take, cairo.ImageSurface):
                    cel.takes[index] = png_b64(take)
                    budget -= 1
                    if budget <= 0:
                        return True          # more to do; come back when idle
        self._compact_source = 0
        return False

    def _switch_scene(self, index):
        self._previous_scene = self.scene_i
        self.scene_i = max(0, min(len(self.doc.scenes) - 1, index))
        self._compact_cels()
        self.sheet = Sheet(self.doc, self.scene_i)
        self.playhead = 0
        self.view_origin = 0
        self.layer_i = 0
        self.selection = None
        self._update_playhead()

    def _rename_scene_prompt(self, *_):
        self._overlay_prompt('Rename Scene…',
                             [('name', 'Name', self.doc.scenes[self.scene_i]['name'], 'text')],
                             'Rename', self._rename_scene_apply)

    def _rename_scene_apply(self, state):
        self._snapshot(_t('Rename Scene'))
        self.doc.scenes[self.scene_i]['name'] = state['name'] or _t('Scene')
        self._commit_change()

    def _scene_floor(self, scene):
        """The shortest this scene can be without stranding work."""
        ends = [run['start'] + run['len']
                for layer in scene['layers'] for run in layer['runs']]
        ends += [sound['start'] + 1 for sound in scene['sounds'] if sound]
        return max(ends) if ends else 1

    def _scene_length_prompt(self, *_):
        scene = self.doc.scenes[self.scene_i]
        floor = self._scene_floor(scene)
        room = self._project_frames() - scene['length']
        self._overlay_prompt(
            'Scene Length…',
            [('length', 'Frames', scene['length'],
              ('int', floor, max(floor, PROJECT_FRAME_MAX - room)))],
            'Set Length', self._scene_length_apply,
            _t('The scene cannot be shorter than %d frames.') % floor)

    def _scene_length_apply(self, state):
        scene = self.doc.scenes[self.scene_i]
        length = min(SCENE_FRAME_MAX, state['length'])
        orphan_run = any(r['start'] + r['len'] > length
                         for layer in scene['layers'] for r in layer['runs'])
        orphan_sound = any(sound and sound['start'] >= length
                           for sound in scene['sounds'])
        if orphan_run or orphan_sound:
            self._flash(_t('The scene cannot be shorter than %d frames.')
                        % self._scene_floor(scene))
            return
        if not self._room_for(length - scene['length']):
            self._flash(_t('The film is at its full length.'))
            return
        self._snapshot(_t('Scene Length'))
        scene['length'] = length
        self.playhead = min(self.playhead, length - 1)
        self._commit_change()

    def _choose_take_prompt(self, *_):
        cel = self._active_cel()
        if not cel:
            return
        run = run_at(self.doc.scenes[self.scene_i]['layers'][self.layer_i]['runs'],
                     self.playhead)
        current = int(run.get('take', 0)) if run else 0
        self._overlay_prompt(
            'Choose Take…',
            [('take', 'Take', min(current, len(cel.takes)), 'take-picker')],
            'Choose', self._choose_take_apply,
            note='Every take in turn keeps the drawing moving. '
                 'A fixed take holds it still.')

    def _choose_take_apply(self, state):
        run = run_at(self.doc.scenes[self.scene_i]['layers'][self.layer_i]['runs'],
                     self.playhead)
        if not run:
            return
        cel = self.doc.cel(run['cel'])
        self._snapshot(_t('Choose Take'))
        run['take'] = max(0, min(len(cel.takes), state['take']))
        self._commit_change()

    def _active_cel(self):
        run = run_at(self.doc.scenes[self.scene_i]['layers'][self.layer_i]['runs'],
                     self.playhead)
        return self.doc.cel(run['cel']) if run else None

    def _wobble_prompt(self, *_):
        if not self._active_cel():
            return
        self._overlay_prompt('Add Wobble Takes…',
                             [('takes', 'Takes (3 or 5)', 3, 'int'),
                              ('strength', 'Strength', 1.1,
                               ('float', .7, 1.8)),
                              ('preview', 'Preview', None,
                               'wobble-preview')],
                             'Add Wobble Takes', self._wobble_apply)

    def _wobble_apply(self, state):
        cel = self._active_cel()
        if not cel:
            return
        count = 5 if state['takes'] >= 5 else 3
        strength = min(1.8, max(.7, state['strength']))
        self._snapshot(_t('Add Wobble Takes'))
        source = cel.decoded(0)
        cel.takes = [source] + [wobble_take(source, cel.id, take, strength)
                               for take in range(2, count + 1)]
        cel.version += 1
        self._commit_change()

    def _recolor_cel(self, *_):
        cel = self._active_cel()
        if not cel or not self.doc.palette:
            return
        self._snapshot(_t('Recolor Drawing to Palette'))
        palette = [(colour, _rgb255(colour)) for colour in self.doc.palette]
        # A drawing holds a handful of colours and a great many pixels, so
        # searching the palette per pixel asks the same question hundreds of
        # thousands of times: 530ms for one take of a filled 320x240, 2117ms
        # at 640x480, and this runs over EVERY take, on the GTK thread.
        # Remember the answer per colour instead.
        nearest = {}
        for take_index_ in range(len(cel.takes)):
            image = cel.decoded(take_index_)
            image.flush()
            data = image.get_data()
            for offset in range(0, len(data), 4):
                if not data[offset + 3]:
                    continue
                rgb = (data[offset + 2], data[offset + 1], data[offset])
                bytes4 = nearest.get(rgb)
                if bytes4 is None:
                    colour, _ = min(palette, key=lambda item: sum(
                        (left - right) ** 2 for left, right in zip(rgb, item[1])))
                    bytes4 = nearest[rgb] = px4(colour)
                data[offset:offset + 4] = bytes4
            image.mark_dirty()
        cel.version += 1
        self._commit_change()

    def _rename_cel_prompt(self, *_, cel_id=None):
        cel = (self.doc.cel(cel_id) if cel_id is not None
               else self._active_cel())
        if cel is None:
            return
        self._rename_cel_target = cel.id
        self._overlay_prompt('Rename Drawing…',
                             [('name', 'Name', cel.name, 'text')],
                             'Rename', self._rename_cel_apply)

    def _rename_cel_apply(self, state):
        cel = self.doc.cel(getattr(self, '_rename_cel_target', -1))
        if cel is None:
            return
        name = state['name'].strip()
        if not name or name == cel.name:
            return
        self._snapshot(_t('Rename Drawing'))
        cel.name = name
        self._commit_change()

    def _rename_layer_prompt(self, *_):
        layer = self.doc.scenes[self.scene_i]['layers'][self.layer_i]
        self._overlay_prompt('Rename Layer…',
                             [('name', 'Name', layer['name'], 'text')],
                             'Rename', self._rename_layer_apply)

    def _rename_layer_apply(self, state):
        layer = self.doc.scenes[self.scene_i]['layers'][self.layer_i]
        name = state['name'].strip()
        if not name or name == layer['name']:
            return
        self._snapshot(_t('Rename Layer'))
        layer['name'] = name
        self._commit_change()

    def _mouth_slots_prompt(self, *_):
        if not self.doc.cels:
            self._flash(_t('The animation has no drawings. Drawing on the canvas makes one.'))
            return
        slots = self.doc.scenes[self.scene_i]['layers'][self.layer_i].get('mouth_slots')
        self._overlay_prompt('Mouth Slots…',
                             [('slots', 'Drawings', list(slots or []),
                               'slots-picker')],
                             'Set Slots', self._mouth_slots_apply,
                             'Slot 1 plays when quiet.')

    def _mouth_slots_apply(self, state):
        known = {cel.id for cel in self.doc.cels}
        slots = [value for value in state['slots'] if value in known][:8]
        self._snapshot(_t('Mouth Slots'))
        self.doc.scenes[self.scene_i]['layers'][self.layer_i]['mouth_slots'] = slots or None
        self._commit_change()

    def _mouth_loudness_prompt(self, *_):
        scene = self.doc.scenes[self.scene_i]
        slots = scene['layers'][self.layer_i].get('mouth_slots') or []
        sound = next((item for item in scene['sounds']
                      if item and not item.get('mute')), None)
        if len(slots) < 3:
            self._flash(_t('Assign at least three mouth slots to the active layer first.'))
            return
        if sound is None:
            self._flash(_t('Add or unmute a sound before making mouths from loudness.'))
            return
        if not os.path.exists(sound['path']):
            # The card would have opened, taken both thresholds, and only
            # then refused — with a line telling someone to add a sound
            # they can see sitting on the sheet. The file is what is gone.
            self._flash(_t('A sound file is missing: %s')
                        % os.path.basename(sound['path']))
            return
        self._overlay_prompt('Mouth from Loudness…',
                             [('quiet', 'Quiet', .10, ('float', 0., 1.)),
                              ('loud', 'Loud', .45, ('float', 0., 1.)),
                              ('preview', 'Preview', None, 'mouth-preview')],
                             'Apply', self._mouth_loudness_apply)

    def _mouth_loudness(self, sound, spf, range_start, range_end):
        """How loud the sound is on each frame of the range, remembered.

        The two thresholds move as a slider moves; this does not depend on
        them at all. Recomputing it per repaint meant squaring and summing
        every sample in the range on the GTK thread — a quarter of a second
        on a 96-frame scene and over a second on a long one, behind the
        preview's 200ms debounce, so the slider fought back the whole time
        someone was trying to find the right value.

        The key names everything the answer depends on: which sound, where
        it starts, where it is trimmed to, the frame rate, and the range.
        """
        key = (sound['path'], tuple(sound.get('sig') or ()), sound['start'],
               sound.get('in_smp', 0), spf, range_start, range_end)
        remembered = getattr(self, '_mouth_rms', None)
        if remembered is not None and remembered[0] == key:
            return remembered[1]
        samples = decode_samples(sound['path'], sound.get('sig'))
        rms = []
        for frame in range(range_start, range_end):
            source_start = ((frame - sound['start']) * spf +
                            sound.get('in_smp', 0))
            if source_start < 0 or source_start >= len(samples):
                rms.append(0.0)
                continue
            block = samples[source_start:min(len(samples), source_start + spf)]
            rms.append(math.sqrt(sum(map(int.__mul__, block, block)) /
                                 max(1, len(block))))
        self._mouth_rms = (key, rms)
        return rms

    def _mouth_preview_slots(self, quiet, loud):
        scene = self.doc.scenes[self.scene_i]
        sound = next((item for item in scene['sounds']
                      if item and not item.get('mute')), None)
        if sound is None:
            return []
        if self.selection:
            _layer, range_start, range_end = self.selection
        else:
            range_start, range_end = 0, scene['length']
        try:
            rms = self._mouth_loudness(sound, SPF[self.doc.fps],
                                       range_start, range_end)
        except Exception:
            return []
        peak = max(rms or [1]) or 1
        lane = [1 if value / peak < quiet else
                2 if value / peak < loud else 3 for value in rms]
        for index in range(len(lane) - 1):
            if (index == 0 or lane[index] != lane[index - 1]) and \
                    lane[index] != lane[index + 1]:
                lane[index] = lane[index + 1]
        return lane

    def _mouth_loudness_apply(self, state):
        scene = self.doc.scenes[self.scene_i]
        slots = scene['layers'][self.layer_i].get('mouth_slots')
        sound = next((item for item in scene['sounds'] if item and not item.get('mute')), None)
        if not slots or len(slots) < 3:
            self._flash(_t('Assign at least three mouth slots to the active layer first.'))
            return
        if not sound:
            self._flash(_t('Add or unmute a sound before making mouths from loudness.'))
            return
        if not os.path.exists(sound['path']):
            self._flash(_t('A sound file is missing: %s')
                        % os.path.basename(sound['path']))
            return
        try:
            samples = decode_samples(sound['path'], sound.get('sig'))
        except Exception:
            # The reason is a decoder detail; the person needs the outcome.
            self._flash(_t('This sound could not be read.'))
            return
        if self.selection:
            _layer, range_start, range_end = self.selection
        else:
            range_start, range_end = 0, scene['length']
        lane = self._mouth_preview_slots(state['quiet'], state['loud'])
        self._snapshot(_t('Mouth from Loudness'))
        layer = scene['layers'][self.layer_i]
        self.sheet.clear(self.layer_i, range_start, range_end)
        for run in slots_to_runs(lane, slots):
            run['start'] += range_start
            layer['runs'].append(run)
        layer['runs'].sort(key=lambda run: run['start'])
        self._commit_change()

    def _toggle_grid(self, *_):
        self.grid = not self.grid
        self.canvas.queue_draw()

    def _cycle_onion(self, *_):
        self.onion = (self.onion + 1) % 3
        self.canvas.queue_draw()

    def _set_zoom(self, zoom):
        self.zoom = zoom
        self._fitted = True
        self.canvas.set_size_request(
            max(1, math.ceil(self.doc.canvas[0] * zoom) + 24),
            max(1, math.ceil(self.doc.canvas[1] * zoom) + 24))
        if hasattr(self, 'zoom_label'):
            self.zoom_label.set_text('%d%%' % round(zoom * 100))
        self.canvas.queue_draw()

    def _fit_canvas(self, *_):
        allocation = self.canvas_scroll.get_allocation()
        width, height = self.doc.canvas
        limit = min(max(1, allocation.width - 24) / width,
                    max(1, allocation.height - 24) / height)
        choices = [step for step in ZOOM_STEPS if step <= limit]
        self.zoom = choices[-1] if choices else ZOOM_STEPS[0]
        self._fitted = True
        self.canvas.set_size_request(max(1, allocation.width - 2),
                                     max(1, allocation.height - 2))
        self.zoom_label.set_text('%d%%' % round(self.zoom * 100))
        self.canvas.queue_draw()

    def _restore_tool_hint(self):
        if not hasattr(self, 'hint'):
            # the dock builds before the status bar: the initial set_active
            # fires this during construction, before there is a bar to write
            return
        self.hint.set_text(_t(TOOL_HINTS[self.tool]))

    def _flash(self, message):
        self.hint.set_text(message)
        if self._flash_timer:
            GLib.source_remove(self._flash_timer)
        self._flash_timer = GLib.timeout_add(2600, self._flash_done)

    def _flash_done(self):
        self._flash_timer = 0
        if self._alive:
            self._restore_tool_hint()
        return False

    def _zoom_step(self, delta):
        index = min(range(len(ZOOM_STEPS)),
                    key=lambda item: abs(ZOOM_STEPS[item] - self.zoom))
        self._set_zoom(ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1,
                                             index + delta))])

    def _copy_frame_image(self, *_):
        image = composite(self.doc, self.doc.scenes[self.scene_i], self.playhead)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        raw = surface_png(image)
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(raw)
        loader.close()
        clipboard.set_image(loader.get_pixbuf())

    def _place_image(self, *_):
        path = nbpicker.open_file(self, title=_t('Place Image'),
                                  start_dir=PICTURES_DIR, patterns=('*.png',))
        if not path:
            return
        cel = self._active_cel()
        if not cel:
            cel, _run = self.sheet.ensure_drawing(self.layer_i, self.playhead)
        try:
            placed = cairo.ImageSurface.create_from_png(path)
        except Exception:
            self._flash(_t('That image could not be opened.'))
            return
        self._snapshot(_t('Place Image'))
        target = cel.decoded(self.active_take.get(cel.id, 0))
        scale = min(1, self.doc.canvas[0] / placed.get_width(),
                    self.doc.canvas[1] / placed.get_height())
        context = cairo.Context(target)
        context.translate((self.doc.canvas[0] - placed.get_width() * scale) / 2,
                          (self.doc.canvas[1] - placed.get_height() * scale) / 2)
        context.scale(scale, scale)
        context.set_source_surface(placed)
        context.get_source().set_filter(cairo.FILTER_NEAREST)
        context.paint()
        cel.version += 1
        self._commit_change()

    def _add_sound(self, *_):
        scene = self.doc.scenes[self.scene_i]
        row = next((index for index, value in enumerate(scene['sounds'])
                    if value is None), None)
        if row is None:
            self._flash(_t('Both sound rows are in use.'))
            return
        path = nbpicker.open_file(self, title=_t('Add Sound'),
                                  start_dir=MUSIC_DIR,
                                  patterns=('*.wav', '*.mp3', '*.ogg', '*.flac'))
        if not path:
            return
        stat_result = os.stat(path)
        sound = {'path': path, 'start': self.playhead, 'in_smp': 0,
                 'out_smp': 0, 'mute': False, 'peaks': '',
                 'sig': [stat_result.st_size, int(stat_result.st_mtime)],
                 'duration_smp': 0, '_peak_token': 0}
        self._snapshot(_t('Add Sound'))
        scene['sounds'][row] = sound
        self._commit_change()
        self._start_peak_worker(sound)

    def _start_peak_worker(self, sound):
        sound['_peak_token'] = sound.get('_peak_token', 0) + 1
        token = sound['_peak_token']

        def work():
            try:
                samples = decode_samples(sound['path'], sound.get('sig'))
                sound_duration = len(samples)
                step = max(1, len(samples) // 512)
                values = []
                for offset in range(0, len(samples), step):
                    block = samples[offset:offset + step]
                    values.extend((min(block), max(block)))
                peaks = array.array('h', values)
                encoded = base64.b64encode(peaks.tobytes()).decode('ascii')
                error = None
            except Exception as exception:
                encoded = ''
                sound_duration = 0
                error = str(exception)
            GLib.idle_add(self._finish_peaks, token, sound, encoded,
                          sound_duration, error)

        worker = threading.Thread(target=work, daemon=True)
        self._workers.append(worker)
        worker.start()

    def _finish_peaks(self, token, sound, encoded, duration, error):
        if not self._alive or token != sound.get('_peak_token'):
            return False
        sound['peaks'] = encoded
        sound['duration_smp'] = duration
        sound['_decode_error'] = bool(error)
        if error:
            # The reason is a decoder detail; the person needs the outcome.
            self._flash(_t('The waveform could not be drawn.'))
        else:
            self._mark_dirty()
        self.timeline.queue_draw()
        return False

    def _record_prompt(self, *_):
        project = os.path.splitext(os.path.basename(self.doc_path or 'animation'))[0]
        take = 1
        while os.path.exists(os.path.join(MUSIC_DIR,
                                          project + ' take %d.wav' % take)):
            take += 1
        self._overlay_prompt('Record Sound…',
                             [('name', 'Name', project + ' take %d' % take,
                               'text')],
                             'Record', self._record_apply,
                             'The recording is saved in Music.')

    def _recording_path(self, name):
        """Where a recording named in the card lands.

        The card asks for a NAME. An entry holding the whole path is
        unreadable at that width — it showed the folder and truncated the
        filename, which is the only part someone chose — and an entry that
        takes a path takes one that points anywhere. A recording is user
        work, so a name already in use gets a number rather than an
        overwrite."""
        clean = os.path.basename((name or '').strip())
        if clean.lower().endswith('.wav'):
            clean = clean[:-4]
        clean = clean.strip(' .')
        if not clean:
            clean = os.path.splitext(
                os.path.basename(self.doc_path or 'animation'))[0] or 'sound'
        destination = os.path.join(MUSIC_DIR, clean + '.wav')
        spare = 2
        while os.path.exists(destination):
            destination = os.path.join(MUSIC_DIR, '%s %d.wav' % (clean, spare))
            spare += 1
        return destination

    def _record_apply(self, state):
        """Start Sequencer's arecord stdout-to-wave pump, copied by behaviour."""
        arecord = shutil.which('arecord')
        if not arecord:
            self._flash(_t('Sound recording is not available.'))
            return
        destination = self._recording_path(state.get('name'))
        os.makedirs(MUSIC_DIR, exist_ok=True)
        device_args = []
        try:
            import nbaudio
            nbaudio.unmute()
            device = nbaudio.capture_device()
            if device:
                device_args = ['-D', device]
        except Exception:
            device_args = []
        process = subprocess.Popen([arecord] + device_args +
                                   ['-q', '-t', 'raw', '-f', 'S16_LE',
                                    '-c', '1', '-r', '48000'],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)

        def pump():
            with wave.open(destination, 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48000)
                while self._alive and process.poll() is None:
                    block = process.stdout.read(4096)
                    if not block:
                        break
                    output.writeframesraw(block)
                    values = array.array('h')
                    values.frombytes(block)
                    peak = max((abs(value) for value in values), default=0) / 32768
                    GLib.idle_add(self._record_level, peak)

        worker = threading.Thread(target=pump, daemon=True)
        self._workers.append(worker)
        worker.start()
        self._record_process = process
        self._record_path = destination
        self._overlay_prompt('Recording',
                             [('meter', 'Input level', 0, 'meter')],
                             'Stop', lambda _state: self._stop_recording(),
                             os.path.basename(destination))

    def _record_level(self, peak):
        if self._alive and hasattr(self, '_record_meter'):
            self._record_meter.set_fraction(max(0, min(1, peak)))
        return False

    def _stop_recording(self):
        process = getattr(self, '_record_process', None)
        if not process:
            return
        process.terminate()
        process.wait(timeout=2)
        del self._record_process
        if os.path.exists(self._record_path):
            scene = self.doc.scenes[self.scene_i]
            row = next((index for index, item in enumerate(scene['sounds'])
                        if item is None), None)
            if row is None:
                # The recording is on disk either way. Saying so is the
                # difference between a full scene and a broken feature.
                self._flash(_t('Every sound row in this scene is full. The '
                               'recording is in Music as “%s”.')
                            % os.path.basename(self._record_path))
            else:
                stat_result = os.stat(self._record_path)
                scene['sounds'][row] = {
                    'path': self._record_path, 'start': self.playhead,
                    'in_smp': 0, 'out_smp': 0, 'mute': False, 'peaks': '',
                    'sig': [stat_result.st_size, int(stat_result.st_mtime)],
                    'duration_smp': stat_result.st_size // 2,
                    '_peak_token': 0}
                self._commit_change()
                self._start_peak_worker(scene['sounds'][row])

    def _onion_surface(self, scene, frame, tint):
        """A neighbouring frame's ink, tinted, on a transparent ground."""
        key = (self.scene_i, frame, tint,
               frame_key(self.doc, scene, frame))
        cached = getattr(self, '_onion_cache', {}).get(key)
        if cached is not None:
            return cached
        base = composite(self.doc, scene, frame, paper=False)
        ctx = cairo.Context(base)
        ctx.set_antialias(cairo.ANTIALIAS_NONE)
        ctx.set_operator(cairo.OPERATOR_ATOP)
        red, green, blue = _rgb255(tint)
        ctx.set_source_rgb(red / 255, green / 255, blue / 255)
        ctx.paint()
        if not hasattr(self, '_onion_cache'):
            self._onion_cache = {}
        if len(self._onion_cache) > 12:
            self._onion_cache.clear()
        self._onion_cache[key] = base
        return base

    def _draw_canvas(self, w, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        self._ensure_canvas_pointer_events()
        self._sync_canvas_cursor()
        if not self._fitted:
            self._fit_canvas()
        scene = self.doc.scenes[self.scene_i]
        preview = self.doc.cel(getattr(self, '_preview_cel', None) or -1)
        if preview is not None:
            s = preview.decoded(0)
        else:
            s = self._cache.get(frame_key(self.doc, scene, self.playhead), lambda: composite(self.doc, scene, self.playhead))
        scale = self.zoom
        x = (w.get_allocated_width() - self.doc.canvas[0] * scale) / 2
        y = (w.get_allocated_height() - self.doc.canvas[1] * scale) / 2
        cr.save()
        cr.translate(x, y)
        cr.scale(scale, scale)
        cr.set_source_surface(s)
        cr.get_source().set_filter(cairo.FILTER_NEAREST if scale >= 1
                                   else cairo.FILTER_BILINEAR)
        cr.paint()
        # Onion skins ride ON TOP of the frame, not under it: the frame
        # carries opaque paper, so anything beneath it is invisible (which
        # is what this feature did before). Previous is signage red, next
        # is the green, both washed — so past and future are told apart at
        # a glance, which is the entire point.
        if self.onion:
            skins = [(self.playhead - 1, '#C8341E')]
            if self.onion == 2:
                skins.append((self.playhead + 1, '#7FA98C'))
            here = frame_key(self.doc, scene, self.playhead)
            for frame, tint in skins:
                if not 0 <= frame < scene['length']:
                    continue
                # An onion skin exists to show CHANGE. A neighbour holding
                # the same drawing — which is most of a held exposure — adds
                # nothing and only washes out the frame being worked on.
                if frame_key(self.doc, scene, frame) == here:
                    continue
                cr.set_source_surface(self._onion_surface(scene, frame, tint))
                cr.get_source().set_filter(cairo.FILTER_NEAREST)
                cr.paint_with_alpha(.35)
        # While mirror is on, show WHERE it folds: the same dashed axis the
        # dock's mark draws, laid on the paper it applies to. Illustrator
        # keeps this in the button only; a beginner benefits from seeing the
        # fold before the stroke rather than after it. Screen chrome — it is
        # drawn after the artwork and never written into a cel.
        if self.symx or self.symy:
            cr.save()
            cr.set_line_width(1 / scale)
            cr.set_dash([4 / scale, 4 / scale])
            cr.set_source_rgba(154 / 255, 148 / 255, 132 / 255, .9)
            # +0.5 lands the hairline on a pixel CENTRE: with antialiasing
            # off, a line straddling a pixel boundary covers neither side
            # and draws nothing at all.
            if self.symx:
                middle = self.doc.canvas[0] // 2 + .5
                cr.move_to(middle, 0)
                cr.line_to(middle, self.doc.canvas[1])
            if self.symy:
                middle = self.doc.canvas[1] // 2 + .5
                cr.move_to(0, middle)
                cr.line_to(self.doc.canvas[0], middle)
            cr.stroke()
            cr.restore()
        # the paper's cut edge: one hairline, so the canvas sits ON the mat
        cr.set_line_width(1 / scale)
        cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
        cr.rectangle(0, 0, self.doc.canvas[0], self.doc.canvas[1])
        cr.stroke()
        if self.grid and scale >= 8:
            cr.set_line_width(1 / scale)
            cr.set_source_rgba(110 / 255, 105 / 255, 94 / 255, .45)
            for px in range(self.doc.canvas[0] + 1):
                cr.move_to(px, 0)
                cr.line_to(px, self.doc.canvas[1])
            for py in range(self.doc.canvas[1] + 1):
                cr.move_to(0, py)
                cr.line_to(self.doc.canvas[0], py)
            cr.stroke()
        selected = self._selected_canvas_bounds()
        if selected is not None:
            cr.set_antialias(cairo.ANTIALIAS_NONE)
            cr.set_line_width(1 / scale)
            cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
            cr.rectangle(selected[0], selected[1],
                         selected[2], selected[3])
            cr.stroke()
        self._draw_canvas_footprint(cr, scale)
        if w.has_focus():
            cr.set_line_width(2 / scale)
            cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
            cr.rectangle(0, 0, self.doc.canvas[0], self.doc.canvas[1])
            cr.stroke()
        cr.restore()
        if preview is None and not any(l['runs'] for l in scene['layers']):
            # the first thing a new person sees: one honest sentence, gone
            # the moment they draw
            hint = _t('Draw here. Each drawing becomes a frame.')
            layout = _pango_layout(cr, hint, 13)
            text_w = layout.get_pixel_size()[0]
            cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
            _show_text(cr, (w.get_allocated_width() - text_w) / 2,
                       w.get_allocated_height() / 2 + 60, hint, 13)
        return False

    def _draw_mark_box(self, cr, x, y, kind, active=False, dim=False):
        """One drawn transport button: a 30x28 letterpress box with a mark.

        Marks are painted, not styled, so they exist on every rendering path
        (Illustrator's _draw_mark rule); the active state is the wash fill the
        Stamp Mouths box already used.
        """
        w_, h_ = 30, 28
        if active and kind != 'mouths':
            cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
            cr.rectangle(x, y, w_, h_)
            cr.fill()
        if kind != 'mouths':
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.rectangle(x + .5, y + .5, w_, h_)
            cr.stroke()
        ink = (154 / 255, 148 / 255, 132 / 255) if dim else (26 / 255, 25 / 255, 22 / 255)
        cr.set_source_rgb(*ink)
        cx, cy = x + w_ / 2, y + h_ / 2
        cr.set_line_width(1.6)
        if kind == 'prev':
            cr.move_to(cx + 4, cy - 6); cr.line_to(cx - 4, cy)
            cr.line_to(cx + 4, cy + 6); cr.close_path(); cr.fill()
            cr.rectangle(cx - 7, cy - 6, 2, 12); cr.fill()
        elif kind == 'next':
            cr.move_to(cx - 4, cy - 6); cr.line_to(cx + 4, cy)
            cr.line_to(cx - 4, cy + 6); cr.close_path(); cr.fill()
            cr.rectangle(cx + 5, cy - 6, 2, 12); cr.fill()
        elif kind == 'play':
            cr.move_to(cx - 4, cy - 7); cr.line_to(cx + 6, cy)
            cr.line_to(cx - 4, cy + 7); cr.close_path(); cr.fill()
        elif kind == 'stop':
            cr.rectangle(cx - 5, cy - 5, 10, 10); cr.fill()
        elif kind == 'loop':
            cr.new_sub_path()
            cr.arc(cx, cy, 6, math.pi * .25, math.pi * 1.75)
            cr.stroke()
            tip_x = cx + 6 * math.cos(math.pi * .25)
            tip_y = cy + 6 * math.sin(math.pi * .25)
            cr.move_to(tip_x + 3, tip_y - 3); cr.line_to(tip_x - 3, tip_y - 2)
            cr.line_to(tip_x + 1, tip_y + 3); cr.close_path(); cr.fill()
        elif kind == 'onion':
            cr.rectangle(cx - 7, cy - 6, 9, 11); cr.stroke()
            cr.rectangle(cx - 2, cy - 3, 9, 11); cr.stroke()
            for dot in range(self.onion):
                cr.arc(cx - 3 + dot * 6, cy + 11, 1.5, 0, math.tau)
                cr.fill()
        elif kind == 'mouths':
            # Two opposed arcs are the open-lips mark; no font glyph is asked
            # to carry this transport meaning (Illustrator's painted-mark law).
            cr.move_to(cx - 6, cy)
            cr.curve_to(cx - 3, cy - 5, cx + 3, cy - 5, cx + 6, cy)
            cr.curve_to(cx + 3, cy + 5, cx - 3, cy + 5, cx - 6, cy)
            cr.stroke()

    def _scene_thumb(self, index):
        """Return a scene card thumb, coalescing stroke-burst rebuilds.

        This is comics' timestamp debounce idiom: no timer runs on the hot
        drawing path.  During a 600 ms burst the previous active thumbnail is
        good chrome; the first later request rebuilds it from the document.
        """
        cached = self._scene_thumbs.get(index)
        if cached is not None:
            return cached
        dirty = getattr(self, '_scene_thumb_dirty', None)
        stale = getattr(self, '_scene_thumb_stale', None)
        if dirty is not None and dirty[0] == index and \
                time.monotonic() - dirty[1] < .6 and \
                stale is not None and stale[0] == index:
            return stale[1]
        try:
            frame = composite(self.doc, self.doc.scenes[index], 0)
        except Exception:
            return None
        thumb = cairo.ImageSurface(cairo.FORMAT_ARGB32, 36, 27)
        ctx = cairo.Context(thumb)
        ctx.scale(36 / self.doc.canvas[0], 27 / self.doc.canvas[1])
        ctx.set_source_surface(frame, 0, 0)
        ctx.paint()
        self._scene_thumbs[index] = thumb
        if dirty is not None and dirty[0] == index:
            self._scene_thumb_dirty = None
            self._scene_thumb_stale = None
        return thumb

    def _draw_timeline(self, w, cr):
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        width_all = w.get_allocated_width()
        cr.set_source_rgb(252 / 255, 251 / 255, 248 / 255)
        cr.paint()
        scene = self.doc.scenes[self.scene_i]
        layers = scene['layers']
        cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
        _show_text(cr, 8, 24, _t('Scenes'))

        # --- the transport: real drawn buttons with real hit targets -------
        self._transport = []
        bx = width_all - 4
        for kind, action in (('mouths', 'mouths'), ('onion', 'onion'),
                             ('loop', 'loop'), ('next', 'next'),
                             ('playstop', 'playstop'), ('prev', 'prev')):
            if kind == 'mouths':
                # The box fits its OWN text: 'Stamp Mouths' is 116px in
                # English and half again in Greek, and drawn text has no
                # widget to ellipsize — ellipsis_sweep inspects Labels, so
                # a fixed box would cut this where no gate could see it.
                mouth_text = _t('Stamp Mouths')
                mouth_layout = _pango_layout(cr, mouth_text, 11)
                box_w = max(116, mouth_layout.get_pixel_size()[0] + 36)
                bx -= box_w
                cr.rectangle(bx + .5, 4.5, box_w, 28)
                if self.stamp_mouths:
                    cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
                    cr.fill_preserve()
                cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
                cr.stroke()
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                self._draw_mark_box(cr, bx + 1, 4, 'mouths')
                _show_text(cr, bx + 26, 23, mouth_text, 11)
                self._transport.append((bx, bx + box_w, action))
                bx -= 6
            else:
                bx -= 30
                mark = kind
                active = False
                if kind == 'playstop':
                    mark = 'stop' if self._playing else 'play'
                elif kind == 'loop':
                    active = self.loop
                elif kind == 'onion':
                    active = self.onion > 0
                self._draw_mark_box(cr, bx, 4, mark, active=active)
                self._transport.append((bx, bx + 30, action))
                bx -= 4
        cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
        readout = self.readout.get_text()
        layout = _pango_layout(cr, readout, 12)
        text_w = layout.get_pixel_size()[0]
        _show_text(cr, bx - 8 - text_w, 24, readout)

        # --- the scene strip: cards with pictures, then the + card --------
        x = 84
        self._scene_cards = []
        limit = bx - 12 - text_w - 40

        def _card_width(index):
            name = self.doc.scenes[index]['name'][:12]
            return 42 + _pango_layout(cr, name, 11).get_pixel_size()[0] + 10

        # The strip always began at scene 1, so a film longer than the bar
        # is wide hid the scene you were IN — no card, no highlight, nothing
        # to point at. The benchmark film has 21 scenes and five fit. Start
        # wherever keeps the current scene on the bar.
        mark_w = _pango_layout(cr, '…', 12).get_pixel_size()[0] + 8
        # Keep the add-a-scene card's room out of the cards' budget: it used
        # to appear or vanish depending on which scene you stood on, and a
        # control that comes and goes by position is not a control.
        at_cap = len(self.doc.scenes) >= SCENE_MAX
        if not at_cap:
            limit -= 36
        # ONE budget for the cards, used by both the loop that decides which
        # scene to start at and the loop that draws them. They had two, and
        # so disagreed: reserving the trailing mark's room in the drawing
        # loop alone dropped one card — the scene you were standing in.
        card_budget = limit - mark_w
        first = 0
        while True:
            span = 84 + (mark_w if first else 0)
            last = first
            for index in range(first, len(self.doc.scenes)):
                width_i = _card_width(index)
                if span + width_i > card_budget:
                    break
                span += width_i + 6
                last = index
            if last >= self.scene_i or first >= self.scene_i:
                break
            first += 1
        if first:
            # earlier scenes exist behind this mark
            cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
            _show_text(cr, x, 24, '…')
            x += mark_w
        for index in range(first, len(self.doc.scenes)):
            entry = self.doc.scenes[index]
            name = entry['name'][:12]
            card_w = _card_width(index)
            # Leave the trailing mark's room out of the cards' budget too.
            # Without this the last card fitted, the '…' was then drawn past
            # the point the add-a-scene card needed, and the '+' silently
            # vanished — but only in the MIDDLE of a long film, which is
            # exactly where someone is when they want another scene. Its
            # reserved room sat there empty.
            if x + card_w > card_budget:
                # the strip is full: say so, and step past the mark so the
                # add-a-scene card cannot land on top of it
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                _show_text(cr, x, 24, '…')
                x += mark_w
                break
            if index == self.scene_i:
                cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
                cr.rectangle(x, 3, card_w, 30)
                cr.fill()
            thumb = self._scene_thumb(index)
            if thumb is not None:
                cr.save()
                cr.translate(x + 3, 4)
                cr.set_source_surface(thumb)
                cr.paint()
                cr.restore()
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.rectangle(x + 2.5, 3.5, 37, 28)
            cr.stroke()
            cr.rectangle(x + .5, 2.5, card_w, 31)
            cr.stroke()
            cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
            _show_text(cr, x + 44, 22, name, 11)
            self._scene_cards.append((x, x + card_w, index))
            x += card_w + 6
        if not at_cap:
            limit += 36
        if x + 30 <= limit:
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.rectangle(x + .5, 2.5, 30, 31)
            cr.stroke()
            ink = (154, 148, 132) if at_cap else (26, 25, 22)
            cr.set_source_rgb(ink[0] / 255, ink[1] / 255, ink[2] / 255)
            cr.rectangle(x + 14, 11, 2, 14)
            cr.fill()
            cr.rectangle(x + 8, 17, 14, 2)
            cr.fill()
            if not at_cap:
                self._scene_cards.append((x, x + 30, 'add'))

        # --- the ruler: numbered frames, marker flags, honest zero --------
        cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
        cr.move_to(0, TL_ROWS_TOP - .5)
        cr.line_to(width_all, TL_ROWS_TOP - .5)
        cr.stroke()
        step = self.column_width
        major = self.doc.fps
        frame = (self.view_origin // 4) * 4
        while True:
            px = TL_GUTTER + (frame - self.view_origin) * step
            if px > width_all:
                break
            if frame % major == 0:
                cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
                cr.move_to(px + .5, TL_ROWS_TOP - 10)
                cr.line_to(px + .5, TL_ROWS_TOP - 1)
                cr.stroke()
                _show_text(cr, px + 3, TL_ROWS_TOP - 8, str(frame), 9)
            elif frame % 4 == 0:
                cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
                cr.move_to(px + .5, TL_ROWS_TOP - 5)
                cr.line_to(px + .5, TL_ROWS_TOP - 1)
                cr.stroke()
            frame += 4
        # column-width stepper at the ruler's right end: wider or tighter
        # frames, the way the artist wants to read time
        widths = (3, 6, 12, 24)
        self._ruler_stepper = []
        for offset, (mark, delta) in enumerate((('-', -1), ('+', 1))):
            sx = width_all - 52 + offset * 26
            sy = TL_STRIP_H + 3
            index = widths.index(self.column_width) if self.column_width in widths else 1
            can = (0 <= index + delta < len(widths))
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.rectangle(sx + .5, sy + .5, 22, TL_RULER_H - 7)
            cr.stroke()
            ink = (26, 25, 22) if can else (154, 148, 132)
            cr.set_source_rgb(ink[0] / 255, ink[1] / 255, ink[2] / 255)
            cr.rectangle(sx + 6, sy + (TL_RULER_H - 7) / 2, 10, 2)
            cr.fill()
            if mark == '+':
                cr.rectangle(sx + 10, sy + (TL_RULER_H - 7) / 2 - 4, 2, 10)
                cr.fill()
            self._ruler_stepper.append((sx, sx + 22, delta if can else 0))
        for marker in scene['markers']:
            marker_x = self._frame_to_x(marker['frame'])
            if marker_x < TL_GUTTER or marker_x > width_all:
                continue
            cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
            cr.move_to(marker_x + .5, TL_STRIP_H + 2)
            cr.line_to(marker_x + .5, TL_ROWS_TOP - 2)
            cr.stroke()
            cr.move_to(marker_x, TL_STRIP_H + 2)
            cr.line_to(marker_x + 8, TL_STRIP_H + 5)
            cr.line_to(marker_x, TL_STRIP_H + 8)
            cr.close_path()
            cr.fill()

        # --- layer rows: bars clipped to the sheet, the gutter painted
        # after, so a scrolled-off bar can never bury a row's name --------
        cr.save()
        cr.rectangle(TL_GUTTER, TL_ROWS_TOP, width_all - TL_GUTTER,
                     (LAYER_MAX + 2) * TL_ROW_H)
        cr.clip()
        # Only the exposures the window can actually show. Cairo would clip
        # the rest, but the work still happens: a 1200-frame scene with 894
        # exposures laid out a Pango run for every one of them and took 53ms
        # a repaint, over the 50ms Article VIII B2 allows, on the path that
        # runs every time the playhead moves. Runs are kept sorted and
        # non-overlapping (F2), so the right-hand edge can stop the loop.
        seen_from = self.view_origin
        seen_to = self.view_origin + max(1, (width_all - TL_GUTTER) // step) + 1
        # During playback GTK invalidates two thin strips around the
        # playhead rather than the sheet, but the handler still built every
        # bar and laid out every name before cairo threw the work away.
        # Read the clip and skip what cannot land inside it.
        clip_x0, _clip_y0, clip_x1, _clip_y1 = cr.clip_extents()
        for display_row, layer_index in enumerate(reversed(range(len(layers)))):
            layer = layers[layer_index]
            y = TL_ROWS_TOP + display_row * TL_ROW_H
            for run in layer['runs']:
                # `<` not `<=`: a run ending exactly at the left edge still
                # draws its right border on the gutter hairline, and culling
                # it rubbed that line out. Proved by comparing the culled
                # and unculled sheets pixel for pixel.
                if run['start'] + run['len'] < seen_from:
                    continue
                if run['start'] > seen_to:
                    break
                left = self._frame_to_x(run['start'])
                width = max(1, run['len'] * step)
                if left > clip_x1 or left + width + 1 < clip_x0:
                    continue
                cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
                cr.rectangle(left, y + 2, width, TL_ROW_H - 4)
                cr.fill()
                cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
                cr.rectangle(left + .5, y + 2.5, width, TL_ROW_H - 4)
                cr.stroke()
                cel = self.doc.cel(run['cel'])
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                if width >= 42:
                    # a name belongs to ITS bar: without this clip a long
                    # drawing name runs across its neighbours and the sheet
                    # reads as one smear of text
                    cr.save()
                    cr.rectangle(left + 2, y + 2, width - 12, TL_ROW_H - 4)
                    cr.clip()
                    _show_text(cr, left + 3, y + 15,
                               cel.name if cel else _t('Missing drawing'), 11)
                    cr.restore()
                if run.get('take', 0) == 0:
                    _show_text(cr, left + width - 10, y + 15, '~', 11)
        gutter_hairline = TL_GUTTER - .5
        cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
        cr.move_to(gutter_hairline, TL_STRIP_H)
        cr.line_to(gutter_hairline, TL_ROWS_TOP + (LAYER_MAX + 2) * TL_ROW_H)
        cr.stroke()
        if not any(l['runs'] for l in layers):
            cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
            _show_text(cr, TL_GUTTER + 24,
                       TL_ROWS_TOP + TL_ROW_H * 2 - 6,
                       _t('Drawings line up here in time.'), 12)

        # --- sound rows: tinted band, named gutter, waveforms -------------
        sound_top = TL_ROWS_TOP + LAYER_MAX * TL_ROW_H
        cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
        cr.rectangle(0, sound_top, width_all, 2 * TL_ROW_H)
        cr.fill()
        for row, sound in enumerate(scene['sounds']):
            y = sound_top + row * TL_ROW_H
            if not sound:
                continue
            left = self._frame_to_x(sound['start'])
            duration = max(0, sound.get('duration_smp', 0) -
                           sound.get('in_smp', 0) - sound.get('out_smp', 0))
            frames = max(1, math.ceil(duration / SPF[self.doc.fps]))
            width = max(step, frames * step)
            if sound.get('_decode_error') or not os.path.exists(sound['path']):
                cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
            else:
                cr.set_source_rgb(127 / 255, 169 / 255, 140 / 255)
            cr.rectangle(left, y + 2, width, TL_ROW_H - 4)
            cr.fill()
            try:
                peaks = array.array('h')
                peaks.frombytes(base64.b64decode(sound.get('peaks', '')))
                pairs = len(peaks) // 2
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                for column in range(max(1, int(width))):
                    pair = min(pairs - 1, int(column * pairs / max(1, width)))
                    low = peaks[pair * 2] / 32768
                    high = peaks[pair * 2 + 1] / 32768
                    cr.move_to(left + column, y + 11 - high * 8)
                    cr.line_to(left + column, y + 11 - low * 8)
                cr.stroke()
            except Exception:
                pass
            if width >= 60:
                # The waveform is drawn in this same ink, through this same
                # band (centred on y+11, reaching 8px either way), so on a
                # loud take the name and the wave came out as one smear. The
                # word gets its own paper: the clip's colour laid back down
                # behind it, so it reads over quiet audio and loud alike.
                name = os.path.basename(sound['path'])[:24]
                layout = _pango_layout(cr, name, 11)
                text_w, text_h = layout.get_pixel_size()
                if sound.get('_decode_error') or not os.path.exists(sound['path']):
                    cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
                else:
                    cr.set_source_rgb(127 / 255, 169 / 255, 140 / 255)
                cr.rectangle(left + 1, y + 15 - text_h + 1,
                             min(width - 2, text_w + 4), text_h)
                cr.fill()
                cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
                _show_text(cr, left + 3, y + 15, name, 11)
            cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
            cr.arc(left + width - 8, y + 11, 3, 0, math.tau)
            if sound.get('mute'):
                cr.fill()
            else:
                cr.stroke()
        if not any(scene['sounds']):
            cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
            _show_text(cr, TL_GUTTER + 24, sound_top + 15,
                       _t('Add a sound from the Sound menu.'), 11)

        # the block a person selected, drawn as the rectangle it is — the
        # sheet said nothing about selection before this
        if self.selection:
            _anchor, sel_start, sel_end = self.selection
            layers_in_block = self._selected_layers()
            left = self._frame_to_x(sel_start)
            right = self._frame_to_x(sel_end)
            for index in layers_in_block:
                if not 0 <= index < len(layers):
                    continue
                display = len(layers) - index - 1
                y = TL_ROWS_TOP + display * TL_ROW_H
                cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
                cr.set_line_width(1)
                cr.rectangle(left + .5, y + 1.5,
                             max(2, right - left) - 1, TL_ROW_H - 4)
                cr.stroke()
        cr.restore()
        # second pass: the gutter — row hairlines, washes, names — above
        # anything the sheet drew
        for display_row, layer_index in enumerate(reversed(range(len(layers)))):
            layer = layers[layer_index]
            y = TL_ROWS_TOP + display_row * TL_ROW_H
            if layer_index == self.layer_i:
                cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
                cr.rectangle(0, y, TL_GUTTER, TL_ROW_H - 1)
                cr.fill()
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.move_to(0, y + TL_ROW_H - .5)
            cr.line_to(width_all, y + TL_ROW_H - .5)
            cr.stroke()
            cr.set_source_rgb(26 / 255, 25 / 255, 22 / 255)
            label = layer['name'][:10]
            if layer.get('mouth_slots'):
                label += ' M'
            _show_text(cr, 8, y + 15, label, 11)
        sound_top_pass = TL_ROWS_TOP + LAYER_MAX * TL_ROW_H
        for row in range(SOUND_ROWS):
            y = sound_top_pass + row * TL_ROW_H
            cr.set_source_rgb(234 / 255, 227 / 255, 210 / 255)
            cr.rectangle(0, y, TL_GUTTER, TL_ROW_H - 1)
            cr.fill()
            cr.set_source_rgb(201 / 255, 196 / 255, 182 / 255)
            cr.move_to(0, y + TL_ROW_H - .5)
            cr.line_to(width_all, y + TL_ROW_H - .5)
            cr.stroke()
            cr.set_source_rgb(110 / 255, 105 / 255, 94 / 255)
            _show_text(cr, 8, y + 15, _t('Sound') + ' %d' % (row + 1), 11)

        # --- the extent band: the whole scene as one strip, the visible
        # window bracketed, so a long scene always says where you are ------
        band_y = TL_ROWS_TOP + (LAYER_MAX + 2) * TL_ROW_H + 2
        band_w = width_all - TL_GUTTER - 8
        length = max(1, scene['length'])
        cr.set_source_rgb(222 / 255, 212 / 255, 194 / 255)
        cr.rectangle(TL_GUTTER, band_y, band_w, 4)
        cr.fill()
        visible = max(1, (width_all - TL_GUTTER) // step)
        left = TL_GUTTER + band_w * self.view_origin / length
        span = max(6, band_w * min(visible, length) / length)
        cr.set_source_rgb(154 / 255, 148 / 255, 132 / 255)
        cr.rectangle(left, band_y, min(span, band_w), 4)
        cr.fill()
        cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
        ph_x = TL_GUTTER + band_w * self.playhead / length
        cr.rectangle(ph_x - 1, band_y - 1, 3, 6)
        cr.fill()
        self._extent_band = (band_y - 3, band_y + 7)

        # --- the playhead, through every band -----------------------------
        x = self._frame_to_x(self.playhead)
        cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
        cr.rectangle(x, TL_STRIP_H + 2, 1,
                     TL_ROWS_TOP - TL_STRIP_H - 2 + (LAYER_MAX + 2) * TL_ROW_H)
        cr.fill()
        cr.move_to(x - 4, TL_STRIP_H + 2)
        cr.line_to(x + 5, TL_STRIP_H + 2)
        cr.line_to(x + .5, TL_STRIP_H + 8)
        cr.close_path()
        cr.fill()
        if w.has_focus():
            cr.set_source_rgb(200 / 255, 52 / 255, 30 / 255)
            cr.rectangle(.5, .5, width_all - 1, w.get_allocated_height() - 1)
            cr.stroke()
        return False

    def _start_playback(self):
        if self._playing:
            return
        self._playing = True
        self._playing_started = time.monotonic()
        if self.stamp_mouths and self.selection:
            _layer, range_start, _range_end = self.selection
            self.playhead = range_start
        self._play_origin = self.playhead
        self._audio_position = self.playhead * SPF[self.doc.fps]
        self._audio_clips = self._scene_audio_clips()
        if self._audio_clips:
            self.audio.start(self._audio_pull)
        self._tick = self.canvas.add_tick_callback(self._play_tick)

    def _stop_playback(self):
        self._playing = False
        self.audio.stop()
        if self.stamp_mouths:
            self.stamp_mouths = False
            self._mouth_pass_open = False
            self._mark_dirty()
        if self._tick:
            self.canvas.remove_tick_callback(self._tick)
            self._tick = 0

    def _play_tick(self, _widget, _clock):
        if not self._playing or not self._alive:
            return GLib.SOURCE_REMOVE
        if self.audio.available and self._audio_clips:
            frame = (self._play_origin +
                     self.audio.position_samples() // SPF[self.doc.fps])
        else:
            elapsed = time.monotonic() - self._playing_started
            frame = self._play_origin + int(elapsed * self.doc.fps)
        scene = self.doc.scenes[self.scene_i]
        loop_end = self.selection[2] if self.stamp_mouths and self.selection \
            else scene['length']
        loop_start = self.selection[1] if self.stamp_mouths and self.selection \
            else 0
        if frame >= loop_end:
            if self.loop:
                self._playing_started = time.monotonic()
                self._play_origin = loop_start
                self._audio_position = loop_start * SPF[self.doc.fps]
                if self._audio_clips:
                    self.audio.start(self._audio_pull)
                frame = loop_start
            elif self.scene_i + 1 < len(self.doc.scenes):
                # Entering the next scene is a fresh playback start: its own
                # sounds, its own sample position, its own frame origin —
                # otherwise the old pump's sample count keeps driving the
                # frame math and the new scene plays silent.
                self.audio.stop()
                self._switch_scene(self.scene_i + 1)
                self._playing_started = time.monotonic()
                self._play_origin = 0
                self._audio_position = 0
                self._audio_clips = self._scene_audio_clips()
                if self._audio_clips:
                    self.audio.start(self._audio_pull)
                frame = 0
            else:
                self._stop_playback()
                return GLib.SOURCE_REMOVE
        if frame != self.playhead:
            self.playhead = frame
            self._update_playhead(targeted=True)
        return GLib.SOURCE_CONTINUE

    def _scene_audio_clips(self):
        clips = []
        for sound in self.doc.scenes[self.scene_i]['sounds']:
            if not sound or sound.get('mute') or not os.path.exists(sound['path']):
                continue
            try:
                decoded = decode_samples(sound['path'], sound.get('sig'))
            except Exception:
                sound['_decode_error'] = True
                self._flash(_t('A sound could not be read, so it plays silent.'))
                continue
            start = sound.get('in_smp', 0)
            end_trim = sound.get('out_smp', 0)
            end = len(decoded) - end_trim if end_trim else len(decoded)
            clips.append((decoded[start:end],
                          sound['start'] * SPF[self.doc.fps]))
        return clips

    def _audio_pull(self, count):
        block = mix_s16(self._audio_clips, self._audio_position, count)
        self._audio_position += len(block)
        scene_end = self.doc.scenes[self.scene_i]['length'] * SPF[self.doc.fps]
        if self._audio_position >= scene_end:
            return array.array('h')
        return block

    def _scrub_frame(self):
        clips = self._scene_audio_clips()
        if not clips:
            return
        start = self.playhead * SPF[self.doc.fps]
        self.audio.play_once(mix_s16(clips, start, SPF[self.doc.fps]))

    def _snapshot(self, label):
        self._undo.append((label, self.scene_i, self.doc.bytes()))
        self._undo = self._undo[-UNDO_DEPTH:]
        self._redo.clear()
        self._trim_history()

    def _trim_history(self):
        """Hold the history inside HISTORY_BYTES by dropping the OLDEST
        frames first.

        UNDO_DEPTH alone is a count, not a size: a document snapshot grows
        with the film, so 200 frames of a cap-sized project is measured in
        gigabytes on a machine chosen for low-spec hardware. Depth is what
        a small project gets; this is what a large one gets instead. The
        most recent frame always survives — an undo that cannot undo the
        last thing done would be worse than a short history."""
        total = sum(len(frame[2]) for frame in self._undo)
        total += sum(len(frame[2]) for frame in self._redo)
        while total > HISTORY_BYTES and len(self._undo) > 1:
            total -= len(self._undo.pop(0)[2])
        while total > HISTORY_BYTES and self._redo:
            total -= len(self._redo.pop(0)[2])

    def _history_apply(self, redo):
        src = self._redo if redo else self._undo
        dst = self._undo if redo else self._redo
        if not src:
            return False
        label, scene, raw = src.pop()
        dst.append((label, self.scene_i, self.doc.bytes()))
        self._trim_history()
        # our own bytes from seconds ago: no need to re-validate them
        self.doc, _ = AnimationDocument.parse(json.loads(raw), strict=False)
        self.scene_i = min(scene, len(self.doc.scenes) - 1)
        self.sheet = Sheet(self.doc, self.scene_i)
        self._mark_dirty()
        self.canvas.queue_draw()
        self.timeline.queue_draw()
        return True

    def _mark_dirty(self):
        self._dirty = True
        self._doc_dirty = True
        self.save_chip.set_text(_t('Editing'))
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(2500, self._autosave)

    def _autosave(self):
        self._save_timer = 0
        if self._store_read_only:
            return False
        try:
            # The recovery store also remembers WHICH film was open, the way
            # writer.py's store carries its path — Article III §3 restores the
            # open document, not merely its contents. Stored portably and
            # only here: a .anim must never name itself, or copying one would
            # change its bytes.
            payload = self.doc.serial()
            payload['doc_path'] = (_portable_path(self.doc_path)
                                   if self.doc_path else None)
            # ...and whether that film has work the file has not seen. Left
            # out, a restart brought the document back under its own name
            # with a clean chip while the file on disk was the older version.
            payload['doc_dirty'] = bool(self._doc_dirty)
            # Where the person was working, not merely what they were
            # working on (Article III §3). Dropping a film-maker back at
            # scene 1 frame 0 of a twenty-scene film loses their place.
            payload['session'] = {
                'scene': self.scene_i, 'frame': self.playhead,
                'layer': self.layer_i, 'zoom': self.zoom,
                'origin': self.view_origin, 'tool': self.tool,
                'colour': self.color, 'size': self.size,
                'columns': self.column_width, 'onion': self.onion}
            os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
            nbapp.atomic_write_json(STORE_FILE, payload)
            self._dirty = False
            self._save_error = None
            # What just reached the disk is the RECOVERY store, not the
            # film. With a document bound, saying "Saved 02:53" while
            # couch.anim is still the older version put the status bar in
            # flat contradiction with the close guard, which says in the
            # same breath that changes to couch.anim are not saved.
            # writer.py already draws this distinction; the words are its.
            self.save_chip.set_text(
                _t('Not saved to file') if (self.doc_path and self._doc_dirty)
                else _t('Saved %s') % time.strftime('%H:%M'))
        except Exception as e:
            self._save_error = e
            self.save_chip.set_text(nbapp.save_failure_reason(e, STORE_FILE))
        return False

    def _save(self, *_):
        if not self.doc_path:
            return self._save_as()
        try:
            save_document(self.doc, self.doc_path)
            self._dirty = False
            self._doc_dirty = False
            self._save_error = None
            self.set_title(_t('Animation') + ' - ' + os.path.basename(self.doc_path))
            return True
        except Exception as e:
            self._save_error = e
            self.save_chip.set_text(nbapp.save_failure_reason(e, self.doc_path))
            return False

    def _save_as(self, *_):
        path = nbpicker.save_file(self, title=_t('Save Animation As'),
                                  start_dir=DOCS_DIR, patterns=('*.anim',),
                                  default_ext='.anim')
        if not path:
            return False
        old_path = self.doc_path
        try:
            save_document(self.doc, path)
        except Exception as exception:
            self.doc_path = old_path
            self._save_error = exception
            self.save_chip.set_text(nbapp.save_failure_reason(exception, path))
            return False
        self.doc_path = path
        self._dirty = False
        self._doc_dirty = False
        self._save_error = None
        self.set_title(_t('Animation') + ' - ' + os.path.basename(path))
        return True

    def _new(self, *_):
        if not self._guard_bypass and self._needs_guard():
            self._guard_document(self._new)
            return
        self._guard_bypass = False
        self._overlay_prompt('New Animation…',
                             [('canvas', 'Canvas', (320, 240),
                               ('choices', tuple((preset,
                                                  '%d × %d' % preset)
                                                 for preset in CANVAS_PRESETS))),
                              ('fps', 'Frames per second', 12,
                               ('choices', tuple((fps, str(fps))
                                                 for fps in FPS_VALUES)))],
                             'Create', self._new_apply,
                             'Size and speed are fixed once the project starts.')

    def _new_apply(self, state):
        self.doc = AnimationDocument(canvas=state['canvas'], fps=state['fps'])
        self.sheet = Sheet(self.doc)
        self.doc_path = None
        self.scene_i = self.layer_i = self.playhead = 0
        self._undo.clear()
        self._redo.clear()
        self._commit_change()

    def _open(self, *_):
        if not self._guard_bypass and self._needs_guard():
            self._guard_document(self._open)
            return False
        self._guard_bypass = False
        path = nbpicker.open_file(self, title=_t('Open Animation'),
                                  start_dir=DOCS_DIR, patterns=('*.anim',))
        if not path:
            return False
        doc, reports = open_document(path)
        if doc is None:
            self._flash(reports[0])
            return False
        self.doc = doc
        self.doc_path = path
        self._store_read_only = False
        self._reports = reports
        self.scene_i = self.layer_i = self.playhead = 0
        self.sheet = Sheet(self.doc)
        self._undo.clear()
        self._redo.clear()
        self._cache.clear()
        self._doc_dirty = False
        self._save_error = None
        self._refresh_lists()
        self._update_playhead()
        self.set_title(_t('Animation') + ' - ' + os.path.basename(path))
        if reports:
            self._flash(reports[0])
        return True

    def _needs_guard(self):
        return bool(self._doc_dirty or self._save_error)

    def _guard_document(self, action):
        def save_then():
            if self._save():
                self._guard_bypass = True
                action()

        def discard_then():
            self._doc_dirty = False
            self._save_error = None
            self._guard_bypass = True
            action()

        self._guard_action = action
        # A confirm states the CONSEQUENCE and names the TARGET (Article
        # IV §3). The old line said what to do and never said which film,
        # which is no help to anyone keeping two of them.
        if self.doc_path:
            stake = _t('Changes to %s are not saved.') % os.path.basename(
                self.doc_path)
        else:
            stake = _t('This film has changes that are not saved.')
        self._overlay_prompt('Unsaved changes', [], 'Save',
                             lambda _state: save_then(), stake)
        layer = self._prompt_layer
        # Add the explicit Discard choice beside the safe Save/Cancel pair —
        # INSIDE the action row. Packing it into the card body left a stray
        # full-width button underneath the row, which read as a mistake.
        discard = Gtk.Button(label=_t('Discard'))
        discard.connect('clicked', lambda *_: (self._close_prompt(),
                                                discard_then()))

        def _action_row(w):
            if isinstance(w, Gtk.Button) and w.get_label() == _t('Cancel'):
                return w.get_parent()
            if isinstance(w, Gtk.Container):
                for child in w.get_children():
                    found = _action_row(child)
                    if found is not None:
                        return found
            return None

        row = _action_row(layer)
        if row is not None:
            row.pack_start(discard, True, True, 0)
            row.reorder_child(discard, 1)     # Cancel | Discard | Save
            discard.show()
        else:
            for child in layer.get_children():
                if isinstance(child, Gtk.EventBox):
                    box = child.get_child()
                    if isinstance(box, Gtk.Box):
                        box.pack_start(discard, False, False, 0)
                        discard.show()

    def _on_delete(self, *_):
        # Closing an UNBOUND film loses nothing: the recovery store carries
        # it, destroy flushes it, and the next launch restores it — the
        # Comics close model. The card here contradicted the status chip
        # ('Saved 15:08' beside 'changes that are not saved') and
        # interrogated a doodle. New/Open KEEP their guard even for unbound
        # films: those replace the recovery film, so their stake is real.
        if self._save_error is None and not self.doc_path:
            return False
        if not self._needs_guard():
            return False
        self._guard_document(self.destroy)
        return True

    def _export(self, *_):
        missing = sorted({os.path.basename(sound['path'])
                          for scene in self.doc.scenes
                          for sound in scene['sounds']
                          if sound and not os.path.exists(sound['path'])})
        if missing:
            self._flash(_t('Export needs these sound files: %s') %
                        ', '.join(missing))
            return
        canvas_width, canvas_height = self.doc.canvas
        largest = max(1, min(1920 // canvas_width, 1080 // canvas_height))
        sizes = (((canvas_width * 2, canvas_height * 2),
                  '%d × %d (2×)' % (canvas_width * 2, canvas_height * 2)),
                 ((canvas_width * largest, canvas_height * largest),
                  '%d × %d (%d×)' % (canvas_width * largest,
                                     canvas_height * largest, largest)),
                 ((1920, 1080), '1920 × 1080 (%d× with borders)' % largest))
        repeats = CONFORM_FPS[self.doc.fps] // self.doc.fps
        self._overlay_prompt('Export Movie…',
                             [('name', 'Name',
                               (os.path.splitext(os.path.basename(
                                   self.doc_path))[0]
                                if self.doc_path else 'animation'), 'text'),
                              ('range', 'Range', 'everything',
                               ('choices', (('everything', _t('Everything')),
                                            ('scene', _t('This scene')),
                                            ('selection', _t('Selection'))))),
                              ('kind', 'Kind', 'video',
                               ('choices', (('video', _t('Video')),
                                            ('gif', 'GIF'),
                                            ('png', _t('PNG frames'))))),
                              ('size', 'Video size', (1920, 1080),
                               ('choices', sizes),
                               ('kind', ('video',), 'This export is not a video.')),
                              ('gif_scale', 'GIF size', 1,
                               ('choices', ((1, '1×'), (2, '2×'), (3, '3×'))),
                               ('kind', ('gif',), 'This export is not a GIF.')),
                              ('native', 'Keep the native frame rate', False, 'check')],
                             'Export', self._export_apply,
                             'Each drawing shows %d frames.' % repeats)

    def _export_apply(self, state):
        frames, audio_specs = self._export_range(state['range'])
        if not frames:
            self._flash(_t('Select exposures before exporting the selection.'))
            return
        if state['kind'] == 'video':
            directory, suffix = VIDEOS_DIR, '.mp4'
        elif state['kind'] == 'gif':
            directory, suffix = PICTURES_DIR, '.gif'
        else:
            directory, suffix = PICTURES_DIR, ''
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, state['name'] + suffix)
        if os.path.exists(path):
            self._overlay_prompt('Replace video?', [], 'Replace',
                                 lambda _ignored: self._export_start(
                                     state, frames, audio_specs, path),
                                 '“%s%s” is already in %s. Exporting replaces it.' %
                                 (state['name'], suffix,
                                  'Videos' if directory == VIDEOS_DIR else 'Pictures'))
            return
        self._export_start(state, frames, audio_specs, path)

    def _export_range(self, range_name):
        if range_name == 'scene':
            chosen = [(self.doc.scenes[self.scene_i], frame)
                      for frame in range(self.doc.scenes[self.scene_i]['length'])]
            selected_scenes = [(self.scene_i, 0, self.doc.scenes[self.scene_i]['length'])]
        elif range_name == 'selection' and self.selection:
            _layer, start, end = self.selection
            chosen = [(self.doc.scenes[self.scene_i], frame)
                      for frame in range(start, end)]
            selected_scenes = [(self.scene_i, start, end)]
        elif range_name == 'selection':
            return ([], [])
        else:
            chosen = [(scene, frame) for scene in self.doc.scenes
                      for frame in range(scene['length'])]
            selected_scenes = [(index, 0, scene['length'])
                               for index, scene in enumerate(self.doc.scenes)]
        audio_specs = []
        output_cursor = 0
        for scene_index, start, end in selected_scenes:
            scene = self.doc.scenes[scene_index]
            for sound in scene['sounds']:
                if not sound or sound.get('mute'):
                    continue
                clip_start = max(start, sound['start'])
                if clip_start >= end:
                    continue
                in_sample = sound.get('in_smp', 0) + \
                    max(0, clip_start - sound['start']) * SPF[self.doc.fps]
                duration = sound.get('duration_smp', 0)
                end_sample = max(in_sample, duration - sound.get('out_smp', 0))
                audio_specs.append({
                    'path': sound['path'], 'in_smp': in_sample,
                    'out_smp': end_sample,
                    'delay_smp': output_cursor +
                    (clip_start - start) * SPF[self.doc.fps]})
            output_cursor += (end - start) * SPF[self.doc.fps]
        return (chosen, audio_specs)

    def _export_start(self, state, frames, audio_specs, path):
        self._cancel.clear()
        self._worker_generation += 1
        generation = self._worker_generation
        self.hint.set_text(_t('Preparing…'))
        self._overlay_prompt('Export Movie…',
                             [('meter', 'Preparing…', 0, 'meter')],
                             'Cancel', lambda _state: self._cancel_export(),
                             path)
        self._export_meter = getattr(self, '_record_meter', None)

        def progress(value):
            GLib.idle_add(self._export_progress, generation, value)

        def work():
            outcome = None
            try:
                if state['kind'] == 'video':
                    export_video(self.doc, frames, path, state['size'][0],
                                 state['size'][1], state['native'], self._cancel,
                                 progress, audio_specs)
                elif state['kind'] == 'gif':
                    export_gif(self.doc, frames, path, state['gif_scale'],
                               self._cancel, progress)
                else:
                    export_png_frames(self.doc, frames, path, self._cancel,
                                      progress)
            except InterruptedError:
                # Stopping on purpose is not a failure, and str() of this
                # is the empty string — which read as success.
                outcome = 'stopped'
            except Exception:
                outcome = 'failed'
            GLib.idle_add(self._export_finished, generation, path, outcome)

        worker = threading.Thread(target=work, daemon=True)
        self._workers.append(worker)
        worker.start()

    def _export_progress(self, generation, value):
        if not self._alive or generation != self._worker_generation:
            return False
        self.hint.set_text(_t('Working - %d%%') % round(value * 100))
        if self._export_meter is not None:
            self._export_meter.set_fraction(max(0, min(1, value)))
        return False

    def _cancel_export(self):
        self._cancel.set()
        self.hint.set_text(_t('Cancelling…'))

    def _export_finished(self, generation, path, outcome):
        if not self._alive or generation != self._worker_generation:
            return False
        if outcome and os.path.isfile(path):
            # A half-written movie is worse than none: it plays for a
            # second and stops, and it sits in Videos looking finished.
            try:
                os.unlink(path)
            except OSError:
                pass
        if outcome == 'stopped':
            self.hint.set_text(_t('Export stopped.'))
        elif outcome:
            # What ffmpeg says here is a codec name and a memory address,
            # in English, on a line this label cannot wrap. The person
            # needs the outcome; the detail is in the exporter's log.
            self.hint.set_text(_t('The export could not be finished.'))
        else:
            self.hint.set_text(_t('Completed: %s') % os.path.basename(path))
        if self._prompt_layer is not None:
            self._close_prompt()
        return False

    def _on_key(self, w, e):
        ctrl = bool(e.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(e.state & Gdk.ModifierType.SHIFT_MASK)
        if self._prompt_layer is not None:
            if e.keyval == Gdk.KEY_Escape:
                self._close_prompt()
                return True
            if ctrl and e.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                if self._prompt_callback is not None:
                    self._apply_prompt(self._prompt_callback,
                                       self._prompt_state)
                return True
            return False
        if nbapp.undo_keys(self.history, e):
            return True
        if ctrl and e.keyval in (Gdk.KEY_s, Gdk.KEY_S):
            # The app took the keystroke whatever the answer was. Reporting
            # a cancelled Save As as unhandled sends Ctrl+Shift+S onward to
            # whatever has focus next.
            if shift:
                self._save_as()
            else:
                self._save()
            return True
        if ctrl and e.keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._new()
            return True
        if ctrl and e.keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self._open()
            return True
        if ctrl and e.keyval in (Gdk.KEY_c, Gdk.KEY_C) and self.selection:
            self._copy_selection()
            return True
        if ctrl and e.keyval in (Gdk.KEY_x, Gdk.KEY_X) and self.selection:
            self._cut_selection()
            return True
        if ctrl and e.keyval in (Gdk.KEY_v, Gdk.KEY_V) and self.sheet.clipboard:
            self._paste_selection()
            return True
        if ctrl and e.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self._repeat_prompt()
            return True
        if ctrl and e.keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self._cycle_onion()
            return True
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return super()._on_key(w, e)
        if self.stamp_mouths and self._playing and \
                Gdk.KEY_0 <= e.keyval <= Gdk.KEY_8:
            self._stamp_mouth(e.keyval - Gdk.KEY_0)
            return True
        if e.keyval == Gdk.KEY_space:
            if self._playing:
                self._stop_playback()
            else:
                self._start_playback()
            return True
        if e.keyval in (Gdk.KEY_comma, Gdk.KEY_period):
            self.playhead = max(0, min(self.doc.scenes[self.scene_i]['length'] - 1, self.playhead + (-self.doc.fps if shift and e.keyval == Gdk.KEY_comma else self.doc.fps if shift else -1 if e.keyval == Gdk.KEY_comma else 1)))
            self._update_playhead()
            self._scrub_frame()
            return True
        if e.keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            self.size = max(1, min(192, self.size +
                                   (-1 if e.keyval == Gdk.KEY_bracketleft else 1)))
            return True
        if e.keyval in (Gdk.KEY_plus, Gdk.KEY_equal) and ctrl:
            self._zoom_step(1)
            return True
        if e.keyval == Gdk.KEY_minus and ctrl:
            self._zoom_step(-1)
            return True
        if e.keyval == Gdk.KEY_0 and ctrl:
            self._fit_canvas()
            return True
        if e.keyval in (Gdk.KEY_g, Gdk.KEY_G):
            self._toggle_grid()
            return True
        if e.keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._new_drawing()
            return True
        if e.keyval in (Gdk.KEY_d, Gdk.KEY_D):
            self._duplicate_drawing()
            return True
        if e.keyval == Gdk.KEY_slash:
            self._split_hold()
            return True
        if e.keyval == Gdk.KEY_equal:
            self._extend_hold()
            return True
        if e.keyval == Gdk.KEY_minus:
            self._shorten_hold()
            return True
        if e.keyval == Gdk.KEY_Delete:
            if self._selected_sound:
                self._remove_sound()
            else:
                self._clear_exposure()
            return True
        if e.keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._marker_prompt()
            return True
        if e.keyval == Gdk.KEY_Home:
            self.playhead = 0
            self._update_playhead()
            return True
        if e.keyval == Gdk.KEY_End:
            self.playhead = self.doc.scenes[self.scene_i]['length'] - 1
            self._update_playhead()
            return True
        if e.keyval == Gdk.KEY_Page_Up:
            self._switch_scene(self.scene_i - 1)
            return True
        if e.keyval == Gdk.KEY_Page_Down:
            self._switch_scene(self.scene_i + 1)
            return True
        if e.keyval in (Gdk.KEY_Left, Gdk.KEY_Right,
                        Gdk.KEY_Up, Gdk.KEY_Down) and self.selection:
            run = run_at(self.doc.scenes[self.scene_i]['layers'][self.layer_i]['runs'],
                         self.playhead)
            if run:
                self._snapshot(_t('Move Exposure'))
                amount = 10 if shift else 1
                run['dx'] += amount * ((e.keyval == Gdk.KEY_Right) -
                                       (e.keyval == Gdk.KEY_Left))
                run['dy'] += amount * ((e.keyval == Gdk.KEY_Down) -
                                       (e.keyval == Gdk.KEY_Up))
                self._commit_change()
                return True
        if e.keyval == Gdk.KEY_Escape and self.selection is not None:
            self.selection = None
            self.canvas.queue_draw()
            self.timeline.queue_draw()
            return True
        return super()._on_key(w, e)

    def _on_destroy(self, *_):
        self._alive = False
        self._cancel.set()
        self._stop_playback()
        source = getattr(self, '_prompt_preview_timer', 0)
        if source:
            GLib.source_remove(source)
            self._prompt_preview_timer = 0
        if getattr(self, '_record_process', None):
            self._record_process.terminate()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        if self._flash_timer:
            GLib.source_remove(self._flash_timer)
            self._flash_timer = 0
        if getattr(self, '_compact_source', 0):
            GLib.source_remove(self._compact_source)
            self._compact_source = 0
        if self._dirty and (not self._store_read_only):
            self._autosave()

def main():
    app = Animation(sys.argv[1] if len(sys.argv) > 1 else None)
    app.show_all()
    Gtk.main()
if __name__ == '__main__':
    main()
