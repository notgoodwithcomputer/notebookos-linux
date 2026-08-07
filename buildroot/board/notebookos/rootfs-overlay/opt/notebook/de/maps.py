#!/usr/bin/env python3
"""
Maps — an offline OpenStreetMap viewer.

Renders a tiled vector street map (.nbm2 packs under /opt/notebook/maps, built
from OSM extracts by mapwork/osm2nbmap3.py) with cairo: roads, water, waterways,
rail, parks/land use and place labels, styled like a street map. Only the map
cells overlapping the current view are decoded (lzma) and cached, so a
continent-sized pack streams from disk instead of loading into RAM. Drag to pan,
scroll or the +/- buttons to zoom, and search cities and towns to jump to them.
Everything is on-disk — no network.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402

import os          # noqa: E402
import math        # noqa: E402
import struct      # noqa: E402
import lzma        # noqa: E402
import json        # noqa: E402
import bisect      # noqa: E402

import nbapp       # noqa: E402
import nbicons     # noqa: E402
from nbi18n import _t  # noqa: E402

MAPS_DIR = "/opt/notebook/maps"


# Every string on this canvas goes through Pango, never cairo's toy text API
# (select_font_face + show_text). The toy API binds ONE face and does no
# per-character fallback, and Nimbus Sans carries no CJK, no Devanagari and no
# Hebrew — so "No maps" and the sentence under it came out as .notdef in five of
# the seventeen shipped languages, and .notdef in that face is INVISIBLE rather
# than a box. A reader in Japanese opened Maps with no pack installed and got a
# blank window with nothing on it to explain why. Pango picks a face per glyph.

def _layout(cr, text, size, bold=False):
    """A Pango layout for `text` at `size` px in the interface face."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription("Nimbus Sans")
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    layout.set_text(text, -1)
    return layout


def _text_w(cr, text, size, bold=False):
    """The drawn width of `text`, for centring and for sizing a plate under it."""
    return _layout(cr, text, size, bold).get_pixel_size()[0]


def _show_text(cr, x, y, text, size, bold=False):
    """Draw `text` with its BASELINE at y — the anchor cr.show_text used, so
    call sites keep the geometry they were tuned with."""
    layout = _layout(cr, text, size, bold)
    cr.move_to(x, y - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)

# category -> (kind, colour, nominal width @street-zoom, casing colour)
# categories match osm2nbmap3: 1 major road, 2 minor road, 3 path, 4 water,
# 5 waterway, 7 green, 8 landuse, 9 rail, 10 coastline, 11 place label.
STYLE = {
    7:  ("fill", (0.80, 0.88, 0.72), 0, None),                # green
    8:  ("fill", (0.93, 0.91, 0.87), 0, None),                # landuse
    4:  ("fill", (0.66, 0.82, 0.90), 0, (0.52, 0.70, 0.82)),  # water
    5:  ("line", (0.60, 0.78, 0.88), 1.6, None),              # waterway
    9:  ("line", (0.55, 0.53, 0.50), 1.4, None),              # rail
    10: ("line", (0.55, 0.72, 0.83), 1.0, None),              # coastline
    3:  ("line", (0.72, 0.64, 0.54), 1.1, None),              # path
    2:  ("line", (1.00, 1.00, 0.98), 3.0, (0.76, 0.73, 0.67)),  # minor road
    1:  ("line", (0.99, 0.85, 0.52), 5.0, (0.79, 0.60, 0.30)),  # major road
    0:  ("line", (0.86, 0.84, 0.80), 1.4, None),              # other road
}
LAND = (0.96, 0.95, 0.92)
# scale (px per mercator-degree) below which a category is hidden. Residential
# streets and paths only appear at street zoom, so regional views show the
# highway/water/green skeleton instead of drowning in white.
MINZOOM = {1: 0, 4: 0, 5: 0, 9: 0, 10: 0, 7: 0, 8: 2500, 2: 7000, 3: 18000, 0: 8000}
LABEL_MINZOOM = {1: 0, 2: 1400, 3: 6000, 4: 16000, 5: 9000}
LABEL_SIZE = {1: 15, 2: 13, 3: 11, 4: 10, 5: 11}
MAXCELLS = 200          # zoom-out is clamped so a view never spans more than this


def _merc(lat, lon):
    """Web-Mercator: returns (x, y) in 'mercator degrees'."""
    lat = max(-85.0, min(85.0, lat))
    y = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))
    return lon, y


def _merc_y_to_lat(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def _rv(buf, i):                          # read unsigned varint -> (value, i)
    shift = val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7


def _rz(buf, i):                          # read zigzag varint
    v, i = _rv(buf, i)
    return (v >> 1) ^ -(v & 1), i


class NBM2:
    """Streaming .nbm2 reader: header + directory in RAM; cells decoded on
    demand (mercator-projected) with a small LRU cache; a places index for
    search and low-zoom labels."""
    CACHE = 64

    def __init__(self, path):
        """Read the header + directory. Raises ValueError — and ONLY ValueError,
        besides the OSError of a file that cannot be opened — for anything this
        is not able to read as a pack.

        ONE ERROR TYPE MATTERS BECAUSE THIS RUNS DURING WINDOW CONSTRUCTION.
        struct.unpack on a short read raises struct.error, which is not a
        ValueError and was not caught, so a HALF-COPIED PACK STOPPED MAPS FROM
        OPENING AT ALL — traceback, no window, no way in to remove the file or
        pick another map. A 2 GB continent pack copied onto a stick is exactly
        the file most likely to arrive truncated, and the copy-it-to-the-Maps-
        folder route is the one the app documents."""
        self.path = path
        f = open(path, "rb")                          # OSError: caller's to own
        self.f = f
        try:
            if f.read(5) != b"NBM2\n":
                raise ValueError("not an .nbm2 pack")
            (nlen,) = struct.unpack("<H", f.read(2))
            self.name = f.read(nlen).decode("utf-8", "replace")
            self.minlat, self.minlon, self.maxlat, self.maxlon = \
                struct.unpack("<4d", f.read(32))
            (cde,) = struct.unpack("<i", f.read(4))
            self.cell_deg = cde / 1e6
            (self.quant,) = struct.unpack("<I", f.read(4))
            (ncells,) = struct.unpack("<I", f.read(4))
            self.places_off, self.places_zlen, self.places_cnt = \
                struct.unpack("<QII", f.read(16))
            self.dir = {}
            for _ in range(ncells):
                cy, cx, off, zl = struct.unpack("<iiQI", f.read(20))
                self.dir[(cy, cx)] = (off, zl)
            if not self.cell_deg:
                raise ValueError("pack declares a zero cell size")
        except ValueError:
            raise
        except Exception as exc:      # struct.error, MemoryError from a bad count
            raise ValueError("unreadable .nbm2 pack: %s" % exc)
        self.payload_base = f.tell()
        self._cache = {}
        self._order = []
        self._places = None

    def cell(self, cy, cx):
        """The features in one cell, or () when it holds none — or when its
        payload is unreadable.

        A corrupt payload must NOT raise: this is called from the draw handler,
        once per visible cell per frame, so a raise here would be a traceback on
        every repaint for as long as the window stayed open. The empty answer is
        cached like any other, so a damaged cell is decoded once and then costs
        nothing."""
        key = (cy, cx)
        c = self._cache.get(key)
        if c is not None:
            return c
        loc = self.dir.get(key)
        if loc is None:
            self._cache[key] = ()
            return ()
        off, zl = loc
        try:
            self.f.seek(self.payload_base + off)
            feats = self._parse(lzma.decompress(self.f.read(zl)))
        except Exception:
            feats = ()
        self._cache[key] = feats
        self._order.append(key)
        if len(self._order) > self.CACHE:
            old = self._order.pop(0)
            if old != key:
                self._cache.pop(old, None)
        return feats

    def _parse(self, raw):
        # Store lat/lon (not mercator): mercator projection is deferred to draw
        # time, so the ~95% of a cell's features that get culled never pay for
        # math.log/tan — ~1.75x faster decode, which matters on the guest.
        q = self.quant
        i = 0
        n, i = _rv(raw, i)
        out = []
        for _ in range(n):
            cat = raw[i]
            flags = raw[i + 1]
            i += 2
            nl, i = _rv(raw, i)
            nm = raw[i:i + nl].decode("utf-8", "replace") if nl else ""
            i += nl
            npts, i = _rv(raw, i)
            pts = []
            qla = qlo = 0
            mnla = mnlo = 1e18
            mxla = mxlo = -1e18
            for _p in range(npts):
                dla, i = _rz(raw, i)
                dlo, i = _rz(raw, i)
                qla += dla
                qlo += dlo
                la = qla / q
                lo = qlo / q
                pts.append((la, lo))
                if la < mnla:
                    mnla = la
                if la > mxla:
                    mxla = la
                if lo < mnlo:
                    mnlo = lo
                if lo > mxlo:
                    mxlo = lo
            out.append((cat, flags, nm, pts, (mnla, mnlo, mxla, mxlo)))
        return out

    def places(self):
        """The searchable place index, or [] when it cannot be read.

        Never raises, for the same reason cell() does not: the FIRST caller is
        _default_view, during window construction, so an unreadable index used
        to mean Maps would not open at all (lzma.LZMAError, "end-of-stream
        marker was reached", from a pack whose payload was truncated or
        zeroed). A pack with no readable index still has a header, so the view
        can still be centred on its bounding box and the streets still draw —
        only the search box and the low-zoom labels go quiet.

        Whatever was decoded before the damage is kept: a truncated index is
        still a usable index for the towns that made it in."""
        if self._places is not None:
            return self._places
        out = []
        try:
            if self.places_cnt:
                self.f.seek(self.payload_base + self.places_off)
                raw = lzma.decompress(self.f.read(self.places_zlen))
                i = 0
                for _ in range(self.places_cnt):
                    rank = raw[i]
                    i += 1
                    nl, i = _rv(raw, i)
                    nm = raw[i:i + nl].decode("utf-8", "replace")
                    i += nl
                    la, lo = struct.unpack_from("<ii", raw, i)
                    i += 8
                    out.append((rank, nm, la / 1e6, lo / 1e6))
        except Exception:
            pass
        self._places = out
        return out


def _startup_pack(maps, cfg):
    """The pack to open on launch: the one the config remembers, when it is
    still installed, else the first one found.

    THE REMEMBERED PACK WAS ONLY EVER HONOURED BY ACCIDENT. Maps opened
    maps[0] and only THEN asked whether the config named that same file, so
    the pack recorded on every pan, zoom and search was obeyed only when it
    happened to sort first. Someone who picked their region from the toolbar,
    found their street and closed the window came back to a different part of
    the world — and the first pan there wrote that view over the position they
    had left, so the place they had found was gone for good.

    A remembered name that is no longer installed (a pack deleted, or the
    stick it lives on unplugged) falls back to the first, and anything in the
    config that is not a string is simply not a remembered pack.
    """
    if not maps:
        return None
    if isinstance(cfg, dict):
        want = cfg.get("pack")
        if isinstance(want, str):
            for _label, path in maps:
                if path == want:
                    return path
    return maps[0][1]


class Maps(nbapp.AppWindow):
    app_name = "Maps"
    menus = ("File", "View")

    def __init__(self):
        super().__init__()
        self._install_css()
        self.maps = self._scan_maps()
        self.pack = None
        self.cx = 0.0
        self.cy = 0.0
        self.scale = 1000.0          # pixels per mercator-degree
        self._drag = None
        self._hi = None              # highlighted (searched) point (mx, my)
        self._empty = None           # (heading, detail) drawn when there is no map
        self._surface = None
        self._surf_size = None
        self._surf_scale = None
        # Device scale the cached surface was rendered at. Part of the cache key
        # so moving the window to a monitor with a different scale re-renders
        # rather than blitting a surface built for the other screen.
        self._surf_dev = None
        self._surf_cx = 0.0
        self._surf_cy = 0.0

        self.content.pack_start(self._toolbar(), False, False, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK
                               | Gdk.EventMask.SCROLL_MASK)
        self.canvas.connect("draw", self._draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("scroll-event", self._on_scroll)
        self.content.pack_start(self.canvas, True, True, 0)
        self._status = Gtk.Label(label="", xalign=0)
        self._status.get_style_context().add_class("mapstatus")
        self.content.pack_start(self._status, False, False, 0)

        if self.maps:
            self._open_map(_startup_pack(self.maps, self._load_cfg()))
        else:
            # Straight through, not deferred: the canvas and every field
            # _show_empty touches already exist here, so an idle source only
            # bought a first frame with no notice on it — and one that could
            # still fire against a canvas the user had already closed.
            self._show_empty()

    # ================= config / packs =================
    def _cfg_path(self):
        base = os.path.join(os.environ.get("NB_HOME", "/root"),
                            ".config", "notebook")
        return os.path.join(base, "maps.json")

    def _load_cfg(self):
        try:
            with open(self._cfg_path()) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_cfg(self):
        if not self.pack:
            return
        # Crash-safe write (temp + fsync + os.replace), the OS-wide pattern —
        # maps was the last persisted file still doing a bare open()+json.dump,
        # which truncates the config before writing. Low stakes (just the view
        # state, and _load_cfg already falls back to {} on a corrupt read) but
        # no reason to be the one non-atomic writer left.
        try:
            nbapp.atomic_write_json(
                self._cfg_path(),
                {"pack": self.pack.path, "cx": self.cx,
                 "cy": self.cy, "scale": self.scale})
        except Exception:
            pass

    def _scan_maps(self):
        # /opt/notebook/maps ships the bundled default; large add-on packs (a
        # whole continent) live in a writable dir so they need not sit inside
        # the read-only squashfs root.
        out = []
        seen = set()
        dirs = [MAPS_DIR,
                os.path.join(os.environ.get("NB_HOME", "/root"), "maps"),
                "/data/maps"]
        for d in dirs:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in names:
                if fn.endswith(".nbm2") and fn not in seen:
                    seen.add(fn)
                    out.append((fn[:-5], os.path.join(d, fn)))
        return out

    # ================= toolbar =================
    def _toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("mapbar")
        if len(self.maps) > 1:
            self._region = Gtk.ComboBoxText()
            for label, path in self.maps:
                self._region.append(path, label.replace("-", " ").title())
            self._region.connect("changed",
                                  lambda c: self._open_map(c.get_active_id()))
            bar.pack_start(self._region, False, False, 0)
        self._search = Gtk.Entry()
        self._search.set_placeholder_text(_t("Search cities and towns…"))
        self._search.get_style_context().add_class("mapsearch")
        self._search.set_width_chars(28)
        self._search.connect("activate", lambda *_: self._do_search())
        bar.pack_start(self._search, False, False, 0)
        sb = Gtk.Button(label=_t("Search"))
        sb.set_relief(Gtk.ReliefStyle.NONE)
        sb.get_style_context().add_class("mapbtn")
        sb.connect("clicked", lambda *_: self._do_search())
        bar.pack_start(sb, False, False, 0)
        bar.pack_start(Gtk.Box(), True, True, 0)
        for lbl, d in (("−", 0.7), ("+", 1.4)):
            b = Gtk.Button(label=lbl)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("mapzoom")
            b.connect("clicked", lambda _w, f=d: self._zoom(f))
            bar.pack_end(b, False, False, 0)
        return bar

    # ================= map load / view =================
    def _open_map(self, path):
        try:
            self.pack = NBM2(path)
            self._position_for(path)
        except Exception:
            # EVERYTHING, because this runs from __init__: a pack that fails in
            # a way not thought of here must leave the window standing with the
            # note below, not stop Maps from opening. NBM2 narrows what it can
            # raise to OSError/ValueError, and cell()/places() no longer raise at
            # all; this is the backstop for whatever is left.
            # A truncated or corrupt pack (a half-copied continent, say) used to
            # leave a blank sheet of paper and not one word about why.
            self.pack = None
            # Name the file AND the way out: "damaged or incomplete" on its own
            # tells a reader what went wrong and nothing about what to do, and
            # the usual cause here is a copy that was interrupted. The second
            # sentence is the SAME line the no-maps state already uses, so the
            # instruction is one wording in every language rather than two.
            self._empty = (
                _t("This map could not be read"),
                (_t("The file %s is damaged or incomplete.")
                 % os.path.basename(path)) + " "
                + _t("Map files are read from the Maps folder in Home."))
            self._invalidate()
            self.canvas.queue_draw()
            return
        self._empty = None
        self._hi = None
        self._invalidate()
        self.canvas.queue_draw()

    def _position_for(self, path):
        """Point the view at the freshly-opened pack: the remembered position if
        the config still names this same pack, else its own default view. Inside
        _open_map's guard, because reading the pack's place index is part of it
        and a damaged index must not escape."""
        cfg = self._load_cfg()
        saved = None
        if isinstance(cfg, dict) and cfg.get("pack") == path:
            try:
                # A hand-edited or half-written config must not put a string
                # where the draw handler expects a number: everything below is
                # arithmetic on these three, every frame. A non-number here is
                # simply "no remembered position", not a damaged map.
                cx, cy = float(cfg["cx"]), float(cfg["cy"])
                scale = float(cfg["scale"])
                if scale > 0 and cx == cx and cy == cy and scale == scale:
                    saved = (cx, cy, scale)
            except (KeyError, TypeError, ValueError):
                saved = None
        if saved is not None:
            self.cx, self.cy, self.scale = saved
        else:
            self._default_view()
        self._invalidate()
        self.canvas.queue_draw()

    def _default_view(self):
        """Open centred on the most important place, at a metro zoom."""
        places = self.pack.places()
        if places:
            rank, nm, la, lo = min(places, key=lambda p: p[0])
            self.cx, self.cy = _merc(la, lo)
            self.scale = 16000.0
        else:
            self.cx = (self.pack.minlon + self.pack.maxlon) / 2
            _, y0 = _merc(self.pack.minlat, 0)
            _, y1 = _merc(self.pack.maxlat, 0)
            self.cy = (y0 + y1) / 2
            self.scale = max(self._min_scale(), 200.0)

    def _min_scale(self):
        """The most zoomed-OUT scale allowed, so a view never spans more than
        MAXCELLS cells. With no map open there is no cell size to measure
        against, so nothing is out of bounds."""
        if not self.pack:
            return 1.0
        w = self.canvas.get_allocated_width() or 900
        h = self.canvas.get_allocated_height() or 600
        return math.sqrt(w * h / MAXCELLS) / self.pack.cell_deg

    def _fit(self):
        if not self.pack:
            return          # View > Fit Region with nothing open: nothing to fit
        b = (self.pack.minlon, self.pack.minlat, self.pack.maxlon, self.pack.maxlat)
        w = self.canvas.get_allocated_width() or 900
        h = self.canvas.get_allocated_height() or 600
        x0, y0 = _merc(b[1], b[0])
        x1, y1 = _merc(b[3], b[2])
        dx = max(1e-6, x1 - x0)
        dy = max(1e-6, y1 - y0)
        self.scale = max(self._min_scale(), min(w / dx, h / dy) * 0.92)
        self.cx = (x0 + x1) / 2
        self.cy = (y0 + y1) / 2

    def _to_screen(self, mx, my, w, h):
        return ((mx - self.cx) * self.scale + w / 2,
                (self.cy - my) * self.scale + h / 2)

    def _to_merc(self, sx, sy, w, h):
        return (self.cx + (sx - w / 2) / self.scale,
                self.cy - (sy - h / 2) / self.scale)

    def _zoom(self, factor, fx=None, fy=None):
        # NO MAP, NOTHING TO ZOOM. The +/- buttons and the scroll wheel are live
        # on the no-map and damaged-pack states too, and they used to reach
        # _min_scale, which read cell_deg off a pack that is None: the button
        # did nothing and put a traceback on the console every time it was
        # pressed, on the very screen a person lands on when a pack will not
        # read. There is nothing to say here — the sheet already says it.
        if not self.pack:
            return
        w = self.canvas.get_allocated_width()
        h = self.canvas.get_allocated_height()
        if fx is None:
            fx, fy = w / 2, h / 2
        bmx, bmy = self._to_merc(fx, fy, w, h)
        self.scale = max(self._min_scale(), min(3.0e5, self.scale * factor))
        amx, amy = self._to_merc(fx, fy, w, h)
        self.cx += bmx - amx
        self.cy += bmy - amy
        self._invalidate()
        self.canvas.queue_draw()
        self._save_cfg()

    # ================= drawing =================
    def _draw(self, w, cr):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        # The DEVICE scale of the screen this widget is on (1, or 2 on a HiDPI
        # panel). Asked of the widget rather than read from a global, because
        # the widget is realised by the time it draws and this is the only
        # answer that is right for the monitor it actually ended up on.
        sf = max(1, int(w.get_scale_factor() or 1))
        need = (self._surface is None
                or self._surf_size != (aw, ah)
                or self._surf_scale != self.scale
                or self._surf_dev != sf)
        if need:
            self._render_surface(aw, ah, sf)
        cr.set_source_rgb(*LAND)
        cr.paint()
        if self._surface is not None:
            dx = (self._surf_cx - self.cx) * self.scale
            dy = (self.cy - self._surf_cy) * self.scale
            cr.set_source_surface(self._surface, dx, dy)
            cr.paint()
        if self._hi is not None:
            hx, hy = self._to_screen(self._hi[0], self._hi[1], aw, ah)
            cr.set_source_rgba(0.78, 0.20, 0.12, 0.9)
            cr.arc(hx, hy, 8, 0, 2 * math.pi)
            cr.set_line_width(3)
            cr.stroke()
        if self.pack is not None:
            self._draw_scale(cr, aw, ah)
        elif self._empty:
            self._draw_empty(cr, aw, ah)
        cr.set_source_rgba(0, 0, 0, 0.4)
        _show_text(cr, 6, ah - 6, "© OpenStreetMap contributors", 9)
        return False

    # A scale bar reads 1, 2 or 5 at some power of ten — the steps every printed
    # map uses, so the number under the bar is one a person can hold in their
    # head and multiply by eye.
    _SCALE_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
                    10000, 20000, 50000, 100000, 200000, 500000, 1000000)

    def _draw_scale(self, cr, aw, ah):
        """A distance scale in the bottom-left corner.

        Without it the map is a pretty picture with no sense of size: the same
        screenful of streets could be a village or a city and nothing on screen
        said which. Longitude IS the x axis here, so a pixel is
        111320·cos(lat)/scale metres — exact at the centre of the view."""
        lat = _merc_y_to_lat(self.cy)
        m_per_px = 111320.0 * math.cos(math.radians(lat)) / max(1e-9, self.scale)
        if not (m_per_px > 0) or m_per_px > 1e7:
            return
        budget = min(150.0, aw * 0.28)          # longest bar we will draw
        metres = self._SCALE_STEPS[0]
        for step in self._SCALE_STEPS:
            if step / m_per_px <= budget:
                metres = step
            else:
                break
        px = metres / m_per_px
        if px < 18:                              # too cramped to read
            return
        label = ("%d m" % metres if metres < 1000
                 else "%g km" % (metres / 1000.0))
        x0, y0 = 12, ah - 26
        tw = _text_w(cr, label, 11)
        # a paper plate under the bar, so it stays legible over dark parkland
        # or water as well as over the pale land fill
        cr.set_source_rgba(0.99, 0.98, 0.96, 0.82)
        cr.rectangle(x0 - 6, y0 - 15, max(px, tw) + 14, 26)
        cr.fill()
        cr.set_source_rgb(0.20, 0.19, 0.17)
        cr.set_line_width(1.4)
        cr.move_to(x0, y0 - 5)
        cr.line_to(x0, y0)
        cr.line_to(x0 + px, y0)
        cr.line_to(x0 + px, y0 - 5)
        cr.stroke()
        _show_text(cr, x0, y0 - 8, label, 11)

    def _visible_cells(self, aw, ah):
        lon0 = self.cx - (aw / 2) / self.scale
        lon1 = self.cx + (aw / 2) / self.scale
        myt = self.cy + (ah / 2) / self.scale
        myb = self.cy - (ah / 2) / self.scale
        latt = _merc_y_to_lat(myt)
        latb = _merc_y_to_lat(myb)
        cd = self.pack.cell_deg
        m = 1                       # margin: features live in their first cell,
        cy0 = int(math.floor(min(latb, latt) / cd)) - m   # but can spill over
        cy1 = int(math.floor(max(latb, latt) / cd)) + m
        cx0 = int(math.floor(lon0 / cd)) - m
        cx1 = int(math.floor(lon1 / cd)) + m
        return cy0, cy1, cx0, cx1, (min(latb, latt), lon0, max(latb, latt), lon1)

    def _render_surface(self, aw, ah, sf=1):
        import cairo
        if aw < 1 or ah < 1 or not self.pack:
            self._surface = None
            return
        # RENDERED AT DEVICE RESOLUTION, DRAWN IN LOGICAL UNITS.
        #
        # The map is a vector renderer, so it has every bit of information
        # needed to draw at the panel's real resolution. This surface used to be
        # allocated at the widget's LOGICAL size and then blitted into a context
        # that GTK had already scaled by 2 on a HiDPI screen -- so the entire
        # map, every road, coastline and label, was upscaled by the compositor.
        # A full-screen vector view was the single largest soft area in the OS,
        # in the one place where sharpness is the whole product.
        #
        # set_device_scale is what makes this a two-line change instead of a
        # rewrite: the surface is allocated with sf times the pixels, but cairo
        # then interprets every coordinate, line width and font size below in
        # LOGICAL units and multiplies internally. So none of the drawing code,
        # the projection maths or the label placement changes at all -- it just
        # lands on a finer grid. The blit in _draw() is unchanged too, because a
        # source surface carrying a device scale is placed at its logical size.
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, aw * sf, ah * sf)
        surf.set_device_scale(sf, sf)
        cr = cairo.Context(surf)
        cr.set_source_rgb(*LAND)
        cr.paint()
        scale = self.scale
        cy0, cy1, cx0, cx1, (vla0, vlo0, vla1, vlo1) = \
            self._visible_cells(aw, ah)

        areas, lines, roads, labels = [], [], [], []
        for ccy in range(cy0, cy1 + 1):
            for ccx in range(cx0, cx1 + 1):
                if (ccy, ccx) not in self.pack.dir:
                    continue
                for f in self.pack.cell(ccy, ccx):
                    cat = f[0]
                    if cat == 11:
                        if scale >= LABEL_MINZOOM.get(f[1], 1e9):
                            labels.append(f)
                        continue
                    if scale < MINZOOM.get(cat, 0):
                        continue
                    bla0, blo0, bla1, blo1 = f[4]      # feature lat/lon bbox
                    if bla1 < vla0 or bla0 > vla1 or blo1 < vlo0 or blo0 > vlo1:
                        continue
                    kind = STYLE.get(cat, STYLE[0])[0]
                    if kind == "fill":
                        areas.append(f)
                    elif cat in (1, 2):
                        roads.append(f)
                    else:
                        lines.append(f)

        def build(pts):
            cr.new_path()
            first = True
            for la, lo in pts:                 # project lat/lon here (deferred)
                my = _merc(la, lo)[1]
                sx = (lo - self.cx) * scale + aw / 2
                sy = (self.cy - my) * scale + ah / 2
                if first:
                    cr.move_to(sx, sy)
                    first = False
                else:
                    cr.line_to(sx, sy)

        for f in areas:
            st = STYLE[f[0]]
            build(f[3])
            cr.close_path()
            cr.set_source_rgb(*st[1])
            cr.fill_preserve()
            if st[3]:
                cr.set_source_rgb(*st[3])
                cr.set_line_width(0.6)
                cr.stroke()
            else:
                cr.new_path()
        for f in lines:
            st = STYLE[f[0]]
            build(f[3])
            cr.set_line_join(1)
            cr.set_line_cap(1)
            cr.set_source_rgb(*st[1])
            cr.set_line_width(max(0.7, st[2]))
            cr.stroke()

        def rw(base):
            return max(0.6, base * max(0.35, min(1.5, scale / 20000.0)))

        for casing in (True, False):
            for f in roads:
                st = STYLE[f[0]]
                lw = rw(st[2])
                if casing and (lw < 2.2 or not st[3]):
                    continue
                build(f[3])
                cr.set_line_join(1)
                cr.set_line_cap(1)
                if casing:
                    cr.set_source_rgb(*st[3])
                    cr.set_line_width(lw + 1.4)
                else:
                    cr.set_source_rgb(*st[1])
                    cr.set_line_width(lw)
                cr.stroke()

        self._draw_labels(cr, labels, aw, ah)
        self._surface = surf
        self._surf_size = (aw, ah)
        self._surf_scale = self.scale
        self._surf_dev = sf
        self._surf_cx = self.cx
        self._surf_cy = self.cy

    def _draw_labels(self, cr, labels, aw, ah):
        labels.sort(key=lambda f: f[1])
        placed = []
        for f in labels:
            la, lo = f[3][0]
            mx, my = _merc(la, lo)
            sx, sy = self._to_screen(mx, my, aw, ah)
            # Cull anything whose NAME would land off the canvas, not just its
            # point: a place sitting a few pixels below the toolbar used to draw
            # a label sliced in half by the top edge.
            if sx < -40 or sx > aw + 40 or sy < 22 or sy > ah + 20:
                continue
            if any(abs(sx - px) < 64 and abs(sy - py) < 16 for px, py in placed):
                continue
            placed.append((sx, sy))
            lay = _layout(cr, f[2], LABEL_SIZE.get(f[1], 11))
            tw = lay.get_pixel_size()[0]
            # The name sits ABOVE its marker, the way every map sets a place
            # name. Centred on the point, the marker dot was punched through the
            # middle of the word ("Fontvi.eille", "Monaco-Vi.lle").
            cr.move_to(sx - tw / 2, sy - 5 - lay.get_baseline() / Pango.SCALE)
            PangoCairo.layout_path(cr, lay)
            cr.set_source_rgba(1, 1, 1, 0.92)
            cr.set_line_width(2.6)
            cr.set_line_join(1)
            cr.stroke_preserve()
            cr.set_source_rgb(0.12, 0.11, 0.10)
            cr.fill()
            cr.arc(sx, sy, 1.7, 0, 2 * math.pi)
            cr.set_source_rgb(0.30, 0.28, 0.25)
            cr.fill()

    def _invalidate(self):
        self._surface = None

    # ================= interaction =================
    def _on_press(self, w, ev):
        if ev.button == 1:
            self._drag = (ev.x, ev.y)
        return True

    def _on_release(self, w, ev):
        if self._drag is not None:
            self._drag = None
            self._invalidate()
            w.queue_draw()
            self._save_cfg()
        return True

    def _on_motion(self, w, ev):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        if self._drag:
            dx = ev.x - self._drag[0]
            dy = ev.y - self._drag[1]
            self._drag = (ev.x, ev.y)
            self.cx -= dx / self.scale
            self.cy += dy / self.scale
            w.queue_draw()
        mx, my = self._to_merc(ev.x, ev.y, aw, ah)
        lat = _merc_y_to_lat(my)
        # Two bare signed decimals meant nothing to anyone who didn't already
        # know they were latitude and longitude. Compass letters say it without
        # a word of jargon, and no minus signs to read. Each hemisphere is its
        # own key so a translator gets the letter WITH its number, rather than
        # a bare "N" that could mean anything.
        ns = (_t("%.4f° N") if lat >= 0 else _t("%.4f° S")) % abs(lat)
        ew = (_t("%.4f° E") if mx >= 0 else _t("%.4f° W")) % abs(mx)
        self._status.set_text("  %s, %s" % (ns, ew))
        return True

    def _on_scroll(self, w, ev):
        f = 1.25 if ev.direction == Gdk.ScrollDirection.UP else 0.8
        self._zoom(f, ev.x, ev.y)
        return True

    def _do_search(self):
        if not self.pack:
            return
        q = (self._search.get_text() or "").strip().lower()
        if not q:
            self._hi = None
            self.canvas.queue_draw()
            return
        best = None
        best_key = None
        for rank, nm, la, lo in self.pack.places():
            low = nm.lower()
            if q not in low:
                continue
            # prefer exact, then higher-rank (lower number), then shorter name
            key = (0 if low == q else 1, rank, len(nm))
            if best_key is None or key < best_key:
                best_key = key
                best = (nm, la, lo)
        if not best:
            # the two-space inset is the status bar's left padding, so it stays
            # OUTSIDE the translated text — a translator must never have to
            # reproduce layout whitespace
            self._status.set_text(
                "  " + _t("No place matching “%s”") % self._search.get_text())
            return
        nm, la, lo = best
        self.cx, self.cy = _merc(la, lo)
        self._hi = (self.cx, self.cy)
        self.scale = max(self.scale, 14000.0)
        self._status.set_text("  " + nm)
        self._invalidate()
        self.canvas.queue_draw()
        self._save_cfg()

    def _show_empty(self):
        # On the canvas, not buried in the status strip, and phrased as
        # something a person can act on rather than as a filesystem path.
        self._empty = (_t("No maps"),
                       _t("Map files are read from the Maps folder "
                          "in Home."))
        self.canvas.queue_draw()
        return False

    def _draw_empty(self, cr, aw, ah):
        """The centred notice shown when there is no map to draw."""
        head, detail = self._empty
        cr.set_source_rgb(0.43, 0.41, 0.37)
        _show_text(cr, (aw - _text_w(cr, head, 19)) / 2, ah / 2 - 14, head, 19)
        cr.set_source_rgb(0.60, 0.58, 0.52)
        # Pango wraps the detail to a reading measure, so a long sentence never
        # runs off both edges of a narrow window. Wrapping it here by splitting
        # on spaces was wrong twice over: Chinese and Japanese put no spaces
        # between words, so the whole sentence stayed one unbreakable "word"
        # and ran straight off both edges.
        measure = max(120, int(min(430, aw - 60)))
        lay = _layout(cr, detail, 13)
        lay.set_width(measure * Pango.SCALE)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_alignment(Pango.Alignment.CENTER)
        cr.move_to((aw - measure) / 2, ah / 2 + 12 - lay.get_baseline() / Pango.SCALE)
        PangoCairo.show_layout(cr, lay)

    # ================= menu =================
    def menu_items(self, name):
        if name == "View":
            return [
                ("Zoom In", lambda: self._zoom(1.4)),
                ("Zoom Out", lambda: self._zoom(0.7)),
                ("Fit Region", lambda: (self._fit(), self._invalidate(),
                                        self.canvas.queue_draw(),
                                        self._save_cfg())),
            ]
        if name == "File":
            return [("Close    Esc", self.close)]
        return super().menu_items(name)

    # ================= css =================
    def _install_css(self):
        css = b"""
        .mapbar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                  padding: 8px 12px; }
        .mapsearch { border: 1px solid #C9C4B6; border-radius: 4px;
                     padding: 5px 9px; background: #FCFBF8; }
        .mapbtn { border: 1px solid #C9C4B6; background: #FCFBF8; color: #1A1916;
                  border-radius: 8px; padding: 5px 12px; box-shadow: none; }
        .mapbtn:hover { background: #F4F2EC; }
        .mapzoom { border: 1px solid #C9C4B6; background: #FCFBF8; color: #1A1916;
                   border-radius: 8px; min-width: 34px; font-size: 17px;
                   box-shadow: none; padding: 0 4px; }
        .mapzoom:hover { background: #F4F2EC; }
        .mapstatus { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                     font-size: 12px; color: #6E695E; padding: 4px 0; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(Maps)
