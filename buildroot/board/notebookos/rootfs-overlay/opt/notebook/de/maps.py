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
import nbmotion    # noqa: E402
import nbi18n
from nbi18n import _t  # noqa: E402

MAPS_DIR = "/opt/notebook/maps"
# The live installation medium, which the live init mounts read-only and leaves
# mounted for the whole session. Big packs (a continent is 2.7 GB) ride the ISO
# as plain files at /maps rather than inside the squashfs root — the ISO carries
# that root twice, so an in-root pack would be stored twice — which makes this
# the place North America actually IS during a live session. There is no such
# directory on an installed machine: the installer copies the packs to
# /data/maps as it installs, so they keep working once the stick is unplugged.
LIVE_MAPS_DIR = "/run/live/medium/maps"


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

# A view's ZOOM TIER: how many of the distinct MINZOOM thresholds its scale has
# passed. The tier -- not the scale -- decides which categories are drawn, so it
# is also what the reader keys its cell cache on and what it tells the parser to
# SKIP. That skip is the single biggest lever in this app: a dense-city cell
# holds ~35k features and a regional view draws 6% of them, so decoding the
# other 94% into Python objects was ~85% of every frame.
_ZOOM_STEPS = tuple(sorted({z for z in MINZOOM.values() if z > 0}))
# tier -> the categories that are hidden at it. A scale in tier t sits in
# [_ZOOM_STEPS[t-1], _ZOOM_STEPS[t]), so a category is hidden exactly when its
# threshold is above the tier's floor. Categories with no MINZOOM entry (place
# labels, anything a future encoder adds) are never hidden.
_TIER_HIDDEN = tuple(
    frozenset(c for c, mz in MINZOOM.items()
              if mz > (_ZOOM_STEPS[t - 1] if t else 0))
    for t in range(len(_ZOOM_STEPS) + 1))


def _zoom_tier(scale):
    t = 0
    for z in _ZOOM_STEPS:
        if scale >= z:
            t += 1
    return t


MAX_ROAD_LABELS = 80      # bounded so a dense downtown cannot stall a frame
MAX_LABEL_CANDIDATES = 700
# Categories whose NAME is worth drawing on the map: roads and paths get their
# name set along the line, water and green areas across their middle. The pack
# has carried a name per feature since the first encoder — the renderer simply
# never drew any of them, so a street map of Chicago came out as correct,
# handsome, unlabelled geometry that you cannot navigate by.
LABELLED_CATS = frozenset((1, 2, 3, 4, 5, 7, 9))
AREA_LABEL_CATS = frozenset((4, 7))
ROAD_LABEL_SIZE = {1: 12, 2: 11, 3: 10, 5: 10, 9: 10}
LABEL_INK_ROAD = (0.20, 0.19, 0.17)
LABEL_INK_AREA = (0.30, 0.36, 0.26)
MAXCELLS = 200          # zoom-out is clamped so a view never spans more than this
PACK_COMPRESSED_MAX = 32 * 1024 * 1024
PACK_CELL_MAX = 32 * 1024 * 1024
PACK_PLACES_MAX = 64 * 1024 * 1024
PACK_PLACES_COUNT_MAX = 500000


def _lzma_limited(data, limit):
    """Decompress one pack member without allowing an expansion bomb."""
    dec = lzma.LZMADecompressor()
    raw = dec.decompress(data, max_length=limit + 1)
    if len(raw) > limit or not dec.eof:
        raise ValueError("map member expands past its limit")
    return raw


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


def _rev_flat(pts):
    """A flat [lat, lon, lat, lon, ...] list, point order reversed."""
    out = []
    for k in range(len(pts) - 2, -1, -2):
        out.append(pts[k])
        out.append(pts[k + 1])
    return out


def _join_ways(fs):
    """Chain features that meet end to end into the longest lines they make.

    Called on the ways that share one name, so the result is that street as a
    street rather than as the block-by-block pieces OSM stores. Endpoints are
    quantised integers straight out of the pack, so ways meeting at a junction
    compare EQUAL — no tolerance, no rounding, and nothing joined that does not
    actually touch."""
    # A pointless feature is dropped here rather than indexed into: a pack with
    # a zero point count is a pack this must not raise on, because the caller is
    # the draw handler.
    fs = [f for f in fs if len(f[3]) >= 2]
    if not fs:
        return []
    if len(fs) == 1:
        return [fs[0][3]]
    ends = {}
    for i, f in enumerate(fs):
        p = f[3]
        ends.setdefault((p[0], p[1]), []).append(i)
        ends.setdefault((p[-2], p[-1]), []).append(i)
    used = [False] * len(fs)
    out = []
    for i in range(len(fs)):
        if used[i]:
            continue
        used[i] = True
        pts = list(fs[i][3])
        # Grow from the tail; then reverse and grow from what was the head, so
        # a way picked up in the middle of a street still reaches both ends.
        for _side in (0, 1):
            while True:
                tail = (pts[-2], pts[-1])
                nxt = None
                for j in ends.get(tail, ()):
                    if not used[j]:
                        nxt = j
                        break
                if nxt is None:
                    break
                used[nxt] = True
                w = fs[nxt][3]
                if (w[0], w[1]) == tail:
                    pts.extend(w[2:])
                else:
                    pts.extend(_rev_flat(w)[2:])
            pts = _rev_flat(pts)
        out.append(pts)
    return out


def _straight_run(P, tol=0.34):
    """The longest nearly-straight run of a projected polyline, as
    ((x0, y0), (x1, y1), chord_length).

    A name has to sit on a straight piece of road: set across a bend it reads
    as crossing the street rather than naming it. A run is extended while each
    new segment stays within `tol` radians (~20 degrees) of the run's own
    direction, which keeps a gently curving avenue in one piece and cuts at a
    real corner."""
    n = len(P)
    if n < 2:
        return None
    best = None
    bestlen = -1.0
    i0 = 0
    for i in range(1, n):
        x, y = P[i]
        px, py = P[i - 1]
        if i - i0 >= 2:
            dx = x - px
            dy = y - py
            cx = px - P[i0][0]
            cy = py - P[i0][1]
            dot = dx * cx + dy * cy
            if dot <= 0 or abs(dx * cy - dy * cx) > tol * dot:
                i0 = i - 1                  # the run bends here: start a new one
        ax, ay = P[i0]
        d = math.hypot(x - ax, y - ay)
        if d > bestlen:
            bestlen = d
            best = ((ax, ay), (x, y))
    if best is None:
        return None
    return best[0], best[1], bestlen


class NBM2:
    """Streaming .nbm2 reader: header + directory in RAM; cells decoded on
    demand (mercator-projected) with a small LRU cache; a places index for
    search and low-zoom labels."""
    CACHE = 64
    # A ceiling on the DIRECTORY, so a pack claiming a wild cell count cannot
    # make the reader allocate its way to death before the first cell is drawn.
    # It has to clear the real packs by a wide margin: North America is 272,226
    # cells at 0.1 degrees, and this was set to 250,000 -- UNDER the one pack the
    # app ships and documents, so Maps refused the continent outright ("map
    # directory is too large") and the release image would have shipped with the
    # bundled map unopenable. A limit that no real input may reach is the only
    # kind worth having; the truncation check below is what actually bounds this
    # against the file, and 1,000,000 cells is a 20 MB directory.
    MAX_DIRECTORY_CELLS = 1000000

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
            if (not all(math.isfinite(v) for v in
                        (self.minlat, self.minlon, self.maxlat, self.maxlon,
                         self.cell_deg))
                    or self.cell_deg <= 0 or self.quant <= 0
                    or self.minlat >= self.maxlat
                    or self.minlon >= self.maxlon):
                raise ValueError("map header has invalid bounds or scale")
            if ncells > self.MAX_DIRECTORY_CELLS:
                raise ValueError("map directory is too large")
            directory_end = f.tell() + ncells * 20
            if directory_end > os.fstat(f.fileno()).st_size:
                raise ValueError("map directory is truncated")
            self.dir = {}
            for _ in range(ncells):
                cy, cx, off, zl = struct.unpack("<iiQI", f.read(20))
                if (cy, cx) in self.dir:
                    raise ValueError("map directory has duplicate cells")
                self.dir[(cy, cx)] = (off, zl)
        except ValueError:
            raise
        except Exception as exc:      # struct.error, MemoryError from a bad count
            raise ValueError("unreadable .nbm2 pack: %s" % exc)
        self.payload_base = f.tell()
        self._cache = {}
        self._order = []
        self._places = None

    def close(self):
        """Release the pack handle; safe across repeated teardown paths."""
        f, self.f = getattr(self, "f", None), None
        if f is not None:
            try:
                f.close()
            except Exception:
                pass

    def cell(self, cy, cx, tier=None):
        """The features in one cell that are DRAWN AT `tier`, or () when it
        holds none — or when its payload is unreadable.

        `tier` is a zoom tier from _zoom_tier(); the features of categories that
        tier hides are skipped in the parser instead of being decoded and then
        thrown away by the renderer. The cache is keyed by tier as well as by
        cell, because the answer differs between them: a tier-0 decode of a cell
        is not a valid tier-4 answer. Passing None decodes everything, which is
        what an inspector or a test that just wants the contents wants.

        A corrupt payload must NOT raise: this is called from the draw handler,
        once per visible cell per frame, so a raise here would be a traceback on
        every repaint for as long as the window stayed open. The empty answer is
        cached like any other, so a damaged cell is decoded once and then costs
        nothing."""
        key = (cy, cx, tier)
        c = self._cache.get(key)
        if c is not None:
            return c
        # A tier's features are a SUBSET of any more complete decode of the same
        # cell, so a cached higher tier answers this by filtering — no seek, no
        # decompress, no parse. Without this, zooming back OUT re-read cells that
        # were already in hand, which the old un-tiered cache never had to do.
        if tier is not None:
            hidden = _TIER_HIDDEN[tier]
            for t2 in list(range(len(_TIER_HIDDEN) - 1, tier, -1)) + [None]:
                got = self._cache.get((cy, cx, t2))
                if got is not None:
                    feats = [f for f in got if f[0] not in hidden]
                    self._cache[key] = feats
                    self._order.append(key)
                    if len(self._order) > self.CACHE:
                        old = self._order.pop(0)
                        if old != key:
                            self._cache.pop(old, None)
                    return feats
        loc = self.dir.get((cy, cx))
        if loc is None:
            self._cache[key] = ()
            return ()
        off, zl = loc
        try:
            if zl > PACK_COMPRESSED_MAX:
                raise ValueError("compressed map cell is too large")
            self.f.seek(self.payload_base + off)
            data = self.f.read(zl)
            if len(data) != zl:
                raise ValueError("truncated map cell")
            hidden = frozenset() if tier is None else _TIER_HIDDEN[tier]
            feats = self._parse(_lzma_limited(data, PACK_CELL_MAX), hidden)
        except Exception:
            feats = ()
        self._cache[key] = feats
        self._order.append(key)
        if len(self._order) > self.CACHE:
            old = self._order.pop(0)
            if old != key:
                self._cache.pop(old, None)
        return feats

    def _parse(self, raw, hidden=frozenset()):
        """Decode one cell into (cat, flags, name, points, bbox) tuples.

        THIS IS THE HOT LOOP OF THE WHOLE APP — it was 85% of every frame at
        every zoom — so it is written against CPython's costs rather than for
        looks:

        * the varint reader is INLINED. _rv/_rz cost two Python calls per
          coordinate, and a dense-city view decodes ~900k coordinates, so the
          call overhead alone outweighed the arithmetic.
        * points stay QUANTISED INTEGERS in a FLAT list, not float pairs in
          tuples. That drops one divide and one tuple allocation per point;
          draw-time divides by `quant` for the few features it actually draws.
        * a feature whose category `hidden` lists is SKIPPED without its
          geometry ever being decoded — the byte scan just counts varint
          terminators past it. Categories, not geometry, are what a zoom tier
          selects on, so the renderer would have discarded every one of these.

        Points are lat/lon, not mercator: projecting at draw time means the
        ~95% of features that get culled never pay for math.log/tan."""
        i = 0
        shift = n = 0
        while True:                                   # feature count
            b = raw[i]
            i += 1
            n |= (b & 0x7F) << shift
            if b < 0x80:
                break
            shift += 7
        out = []
        append = out.append
        for _ in range(n):
            cat = raw[i]
            flags = raw[i + 1]
            i += 2
            shift = nl = 0
            while True:                               # name length
                b = raw[i]
                i += 1
                nl |= (b & 0x7F) << shift
                if b < 0x80:
                    break
                shift += 7
            if nl:
                nm = raw[i:i + nl].decode("utf-8", "replace")
                i += nl
            else:
                nm = ""
            shift = npts = 0
            while True:                               # point count
                b = raw[i]
                i += 1
                npts |= (b & 0x7F) << shift
                if b < 0x80:
                    break
                shift += 7

            if cat in hidden:
                # Not drawn at this zoom: step over 2*npts varints by counting
                # terminator bytes. No ints, tuples or lists are built.
                need = npts + npts
                while need:
                    if raw[i] < 0x80:
                        need -= 1
                    i += 1
                continue

            pts = []
            add = pts.append
            qla = qlo = 0
            mnla = mnlo = 1 << 62
            mxla = mxlo = -(1 << 62)
            for _p in range(npts):
                b = raw[i]                            # zigzag varint: dlat
                i += 1
                if b < 0x80:
                    v = b
                else:
                    v = b & 0x7F
                    shift = 7
                    while True:
                        b = raw[i]
                        i += 1
                        v |= (b & 0x7F) << shift
                        if b < 0x80:
                            break
                        shift += 7
                qla += (v >> 1) ^ -(v & 1)
                b = raw[i]                            # zigzag varint: dlon
                i += 1
                if b < 0x80:
                    v = b
                else:
                    v = b & 0x7F
                    shift = 7
                    while True:
                        b = raw[i]
                        i += 1
                        v |= (b & 0x7F) << shift
                        if b < 0x80:
                            break
                        shift += 7
                qlo += (v >> 1) ^ -(v & 1)
                add(qla)
                add(qlo)
                if qla < mnla:
                    mnla = qla
                if qla > mxla:
                    mxla = qla
                if qlo < mnlo:
                    mnlo = qlo
                if qlo > mxlo:
                    mxlo = qlo
            append((cat, flags, nm, pts, (mnla, mnlo, mxla, mxlo)))
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
                if self.places_cnt > PACK_PLACES_COUNT_MAX:
                    raise ValueError("place index contains too many records")
                if self.places_zlen > PACK_COMPRESSED_MAX:
                    raise ValueError("compressed place index is too large")
                self.f.seek(self.payload_base + self.places_off)
                data = self.f.read(self.places_zlen)
                if len(data) != self.places_zlen:
                    raise ValueError("truncated place index")
                raw = _lzma_limited(data, PACK_PLACES_MAX)
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


def _startup_packs(maps, cfg):
    """Candidate paths in honest startup order, without trusting validity."""
    if not maps:
        return []
    remembered = None
    if isinstance(cfg, dict):
        want = cfg.get("pack")
        if isinstance(want, str) and any(path == want for _label, path in maps):
            remembered = want

    def _bytes(pair):
        try:
            return os.path.getsize(pair[1])
        except OSError:
            return -1

    ordered = [path for _label, path in sorted(maps, key=_bytes, reverse=True)]
    if remembered is not None:
        ordered.remove(remembered)
        ordered.insert(0, remembered)
    return ordered


def _startup_pack(maps, cfg):
    """The pack to open on launch: the one the config remembers, when it is
    still installed, else the LARGEST one installed.

    THE REMEMBERED PACK WAS ONLY EVER HONOURED BY ACCIDENT. Maps opened
    maps[0] and only THEN asked whether the config named that same file, so
    the pack recorded on every pan, zoom and search was obeyed only when it
    happened to sort first. Someone who picked their region from the toolbar,
    found their street and closed the window came back to a different part of
    the world — and the first pan there wrote that view over the position they
    had left, so the place they had found was gone for good.

    A remembered name that is no longer installed (a pack deleted, or the
    stick it lives on unplugged) falls back the same way, and anything in the
    config that is not a string is simply not a remembered pack.

    WITH NOTHING REMEMBERED, SIZE DECIDES — not scan order. /opt/notebook/maps
    ships a 44 KB sample of Monaco and is the first directory scanned, so "the
    first one found" meant a machine carrying the whole of North America still
    opened on one small town on the Riviera. From the desk that does not read
    as a default worth changing; it reads as the map data not having loaded.
    Size is the honest proxy for "the substantial one" here, and reading it is
    a stat, not a decode of a 2.7 GB pack.
    """
    candidates = _startup_packs(maps, cfg)
    return candidates[0] if candidates else None


def _open_startup(app, maps, cfg):
    """Try installed packs in preference order; damaged packs are skipped."""
    for path in _startup_packs(maps, cfg):
        if app._open_map(path):
            return path
    return None


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
        self._view_anim = None
        self._view_gen = 0
        self._view_moving = False
        self.connect("destroy", self._on_destroy)

        self.content.pack_start(self._toolbar(), False, False, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_can_focus(True)
        self.canvas.set_tooltip_text(_t("Maps"))
        try:
            self.canvas.get_accessible().set_name(_t("Maps"))
        except Exception:
            pass
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK
                               | Gdk.EventMask.SCROLL_MASK)
        self.canvas.connect("draw", self._draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("scroll-event", self._on_scroll)
        self.canvas.connect("key-press-event", self._on_canvas_key)
        self.content.pack_start(self.canvas, True, True, 0)
        self._status = Gtk.Label(label="", xalign=0)
        self._status.get_style_context().add_class("mapstatus")
        self.content.pack_start(self._status, False, False, 0)

        if self.maps:
            _open_startup(self, self.maps, self._load_cfg())
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
        self._extra = {}
        path = self._cfg_path()
        self._cfg_writable = not os.path.exists(path)
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            moved = nbapp.preserve_damaged(path)
            self._cfg_writable = bool(moved) or not os.path.exists(path)
            return {}
        if not isinstance(data, dict):
            # Valid JSON can still be unusable to this app.  The shared
            # parse-level damage guard cannot recognize a wrong top-level
            # shape, so move it aside before the next view-state save.
            moved = nbapp.quarantine_unrecognized(path)
            self._cfg_writable = bool(moved) or not os.path.exists(path)
            return {}
        self._cfg_writable = True
        known = {"pack", "cx", "cy", "scale", "_extra"}
        nested = data.get("_extra")
        if isinstance(nested, dict):
            self._extra.update(nested)
        for key, value in data.items():
            if key not in known:
                self._extra[key] = value
        return data

    def _save_cfg(self):
        if not self.pack:
            return
        # A valid-but-foreign store may still be sitting at this path when its
        # protective quarantine failed (read-only media, permissions, full
        # directory). Never replace the only copy with our fallback view.
        if not getattr(self, "_cfg_writable", True):
            path = self._cfg_path()
            nbapp.quarantine_unrecognized(path)
            if os.path.exists(path):
                nbapp.note_save_failure(
                    self, OSError("could not preserve unreadable map settings"),
                    path)
                return False
            self._cfg_writable = True
        # Crash-safe write (temp + fsync + os.replace), the OS-wide pattern —
        # maps was the last persisted file still doing a bare open()+json.dump,
        # which truncates the config before writing. Low stakes (just the view
        # state, and _load_cfg already falls back to {} on a corrupt read) but
        # no reason to be the one non-atomic writer left.
        try:
            nbapp.atomic_write_json(
                self._cfg_path(),
                {"pack": self.pack.path, "cx": self.cx,
                 "cy": self.cy, "scale": self.scale,
                 "_extra": getattr(self, "_extra", {})})
            return True
        except Exception as exc:
            nbapp.note_save_failure(self, exc, self._cfg_path())
            return False

    def _scan_maps(self):
        # /opt/notebook/maps ships the bundled default; large add-on packs (a
        # whole continent) live in a writable dir so they need not sit inside
        # the read-only squashfs root.
        out = []
        seen = set()
        # Order is a preference, because `seen` keeps the FIRST pack of a given
        # name: a copy on the machine's own disk beats the same pack on the
        # installation medium, which may be a DVD or a slow USB stick.
        dirs = [MAPS_DIR,
                os.path.join(os.environ.get("NB_HOME", "/root"), "maps"),
                "/data/maps",
                LIVE_MAPS_DIR]
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
            self._region.connect("changed", self._on_region_changed)
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
        for lbl, d, action in (("−", 0.7, _t("Zoom out")),
                               ("+", 1.4, _t("Zoom in"))):
            b = Gtk.Button(label=lbl)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("mapzoom")
            b.set_tooltip_text(action)
            b.get_accessible().set_name(action)
            b.connect("clicked", lambda _w, f=d: self._zoom(f))
            bar.pack_end(b, False, False, 0)
        return bar

    def _on_region_changed(self, combo):
        """Persist a successfully selected pack even without a later pan."""
        if getattr(self, "_changing_region", False):
            return
        path = combo.get_active_id()
        if not path:
            return
        old_path = getattr(getattr(self, "pack", None), "path", None)
        if self._open_map(path):
            self._save_cfg()
            return
        # _open_map deliberately kept the old canvas. Keep the selector in
        # agreement with it as well, without recursively reopening the pack.
        self._changing_region = True
        try:
            if old_path:
                combo.set_active_id(old_path)
            else:
                combo.set_active(-1)
        finally:
            self._changing_region = False

    # ================= map load / view =================
    def _open_map(self, path):
        # A region choice supersedes travel in the old pack. The new pack still
        # opens immediately; motion never owns functional state.
        self._view_gen += 1
        if self._view_anim is not None:
            self._view_anim.cancel()
            self._view_anim = None
        self._view_moving = False
        old = self.pack
        candidate = None
        try:
            candidate = NBM2(path)
            self.pack = candidate
            self._position_for(path)
        except Exception:
            # EVERYTHING, because this runs from __init__: a pack that fails in
            # a way not thought of here must leave the window standing with the
            # note below, not stop Maps from opening. NBM2 narrows what it can
            # raise to OSError/ValueError, and cell()/places() no longer raise at
            # all; this is the backstop for whatever is left.
            # A truncated or corrupt pack (a half-copied continent, say) used to
            # leave a blank sheet of paper and not one word about why.
            if candidate is not None:
                candidate.close()
            self.pack = old
            # Name the file AND the way out: "damaged or incomplete" on its own
            # tells a reader what went wrong and nothing about what to do, and
            # the usual cause here is a copy that was interrupted. The second
            # sentence is the SAME line the no-maps state already uses, so the
            # instruction is one wording in every language rather than two.
            problem = (
                _t("This map could not be read"),
                (_t("The file %s is damaged or incomplete.")
                 % os.path.basename(path)) + " "
                + _t("Map files are read from the Maps folder in Home."))
            # A bad choice must not blank the valid region already on screen.
            # Keep that map visible and put the failure in its status strip;
            # on startup there is no old map, so the full empty card remains.
            self._empty = problem if old is None else None
            if old is not None:
                try:
                    self._status.set_text(problem[0] + ". " + problem[1])
                except Exception:
                    pass
            self._invalidate()
            self.canvas.queue_draw()
            return False
        if old is not None and old is not candidate:
            old.close()
        self._empty = None
        self._hi = None
        self._invalidate()
        self.canvas.queue_draw()
        return True

    def _on_destroy(self, *_):
        if self._view_anim is not None:
            self._view_anim.cancel()
            self._view_anim = None
        pack, self.pack = self.pack, None
        if pack is not None:
            pack.close()
        return False

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
        scale = max(self._min_scale(), min(w / dx, h / dy) * 0.92)
        self._animate_view((x0 + x1) / 2, (y0 + y1) / 2, scale)

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
        scale = max(self._min_scale(), min(3.0e5, self.scale * factor))
        # Solve the target centre directly so the mercator point under the
        # pointer remains under that same pixel for the entire zoom.
        cx = bmx - (fx - w / 2) / scale
        cy = bmy + (fy - h / 2) / scale
        self._animate_view(cx, cy, scale,
                           anchor=(bmx, bmy, fx, fy, w, h))

    # The renderer's viewport is the moving layer; container transitions would
    # be both semantically wrong and much more expensive here.
    # nbmotion-inventory: content.maps
    def _animate_view(self, cx1, cy1, scale1, duration=None, anchor=None):
        """Travel to a viewport using the last vector raster as a cheap layer.

        The expensive map is rendered at most once before travel (only when no
        valid raster exists) and once on landing. Intermediate frames are an
        affine blit in _draw, including on the software renderer. Policy-still
        follows this same path and lands synchronously and exactly (§F4).
        """
        if not self.pack:
            return False
        cx1, cy1, scale1 = float(cx1), float(cy1), float(scale1)
        cx0, cy0, scale0 = self.cx, self.cy, self.scale
        if (abs(cx1 - cx0) < 1e-12 and abs(cy1 - cy0) < 1e-12
                and abs(scale1 - scale0) < 1e-9):
            return False

        aw = self.canvas.get_allocated_width()
        ah = self.canvas.get_allocated_height()
        sf = max(1, int(self.canvas.get_scale_factor() or 1))
        if (self._surface is None or self._surf_size != (aw, ah)
                or self._surf_scale != scale0 or self._surf_dev != sf):
            self._render_surface(aw, ah, sf)

        self._view_gen += 1
        gen = self._view_gen
        old = self._view_anim
        self._view_anim = None
        if old is not None:
            old.cancel()
        log0, log1 = math.log(scale0), math.log(scale1)
        self._view_moving = True

        def on_frame(e):
            if gen != self._view_gen:
                return
            self.scale = math.exp(log0 + (log1 - log0) * e)
            if anchor is None:
                self.cx = cx0 + (cx1 - cx0) * e
                self.cy = cy0 + (cy1 - cy0) * e
            else:
                mx, my, fx, fy, width, height = anchor
                self.cx = mx - (fx - width / 2) / self.scale
                self.cy = my + (fy - height / 2) / self.scale
            # Every pixel of the map layer moves, but the toolbar and status do
            # not: damage the canvas allocation, never the whole window.
            self.canvas.queue_draw_area(0, 0, aw, ah)

        def on_done(_finished):
            if gen != self._view_gen:
                return
            self.cx, self.cy, self.scale = cx1, cy1, scale1
            self._view_moving = False
            self._view_anim = None
            self._invalidate()
            self.canvas.queue_draw_area(0, 0, aw, ah)
            self._save_cfg()

        self._view_anim = nbmotion.animate(
            self.canvas, on_frame, 0.0, 1.0,
            duration=duration or nbmotion.PAGE,
            easing=nbmotion.ARRIVE, on_done=on_done)
        return True

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
                or (not self._view_moving and self._surf_scale != self.scale)
                or self._surf_dev != sf)
        if need:
            self._render_surface(aw, ah, sf)
        cr.set_source_rgb(*LAND)
        cr.paint()
        if self._surface is not None:
            ratio = self.scale / self._surf_scale
            dx = aw / 2 + (self._surf_cx - self.cx) * self.scale - ratio * aw / 2
            dy = ah / 2 + (self.cy - self._surf_cy) * self.scale - ratio * ah / 2
            cr.save()
            cr.translate(dx, dy)
            cr.scale(ratio, ratio)
            cr.set_source_surface(self._surface, 0, 0)
            cr.paint()
            cr.restore()
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

        # Cull against the view in QUANTISED units, the form the parser leaves
        # points in, so the comparison costs no conversion per feature.
        q = self.pack.quant
        qla0, qlo0 = vla0 * q, vlo0 * q
        qla1, qlo1 = vla1 * q, vlo1 * q
        tier = _zoom_tier(scale)
        areas, lines, roads, labels, named = [], [], [], [], []
        for ccy in range(cy0, cy1 + 1):
            for ccx in range(cx0, cx1 + 1):
                if (ccy, ccx) not in self.pack.dir:
                    continue
                for f in self.pack.cell(ccy, ccx, tier):
                    cat = f[0]
                    if cat == 11:
                        if scale >= LABEL_MINZOOM.get(f[1], 1e9):
                            labels.append(f)
                        continue
                    if scale < MINZOOM.get(cat, 0):
                        continue
                    bla0, blo0, bla1, blo1 = f[4]      # quantised lat/lon bbox
                    if bla1 < qla0 or bla0 > qla1 or blo1 < qlo0 or blo0 > qlo1:
                        continue
                    kind = STYLE.get(cat, STYLE[0])[0]
                    if kind == "fill":
                        areas.append(f)
                    elif cat in (1, 2):
                        roads.append(f)
                    else:
                        lines.append(f)
                    if f[2] and cat in LABELLED_CATS:
                        named.append(f)

        # Hoisted into locals: build() runs once per drawn feature and the
        # projection runs once per POINT, and an attribute lookup per point is
        # not free at this count.
        iq = 1.0 / q
        scx, scy = self.cx, self.cy
        hw, hh = aw / 2.0, ah / 2.0
        move_to, line_to = cr.move_to, cr.line_to
        log, tan, radians, degrees = math.log, math.tan, math.radians, math.degrees
        qpi = math.pi / 4
        qlat_max = int(85.0 * q)

        def build(pts):
            cr.new_path()
            k = 0
            npt = len(pts)
            first = True
            while k < npt:
                ila = pts[k]
                # Clamp in quantised space: a damaged pack can hold a latitude
                # past the pole, where log(tan(...)) takes the log of a negative
                # number and RAISES — inside the draw handler, on every repaint.
                if ila > qlat_max:
                    ila = qlat_max
                elif ila < -qlat_max:
                    ila = -qlat_max
                lo = pts[k + 1] * iq
                k += 2
                my = degrees(log(tan(qpi + radians(ila * iq) / 2)))
                sx = (lo - scx) * scale + hw
                sy = (scy - my) * scale + hh
                if first:
                    move_to(sx, sy)
                    first = False
                else:
                    line_to(sx, sy)

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

        # Labels last, over finished geometry, and in importance order: a town
        # name outranks a street name outranks a park name, and each pass adds
        # what it placed to `taken` so the next one gives way to it rather than
        # printing through it.
        taken = self._draw_labels(cr, labels, aw, ah)
        self._draw_named_labels(cr, named, aw, ah, taken)
        self._surface = surf
        self._surf_size = (aw, ah)
        self._surf_scale = self.scale
        self._surf_dev = sf
        self._surf_cx = self.cx
        self._surf_cy = self.cy

    def _draw_labels(self, cr, labels, aw, ah):
        """Place names. Returns the boxes it occupied, so the street and area
        passes can keep clear of them."""
        labels.sort(key=lambda f: f[1])
        iq = 1.0 / self.pack.quant
        placed = []
        taken = []
        for f in labels:
            la, lo = f[3][0] * iq, f[3][1] * iq
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
            tw, th = lay.get_pixel_size()
            taken.append((sx - tw / 2 - 3, sy - 8 - th, sx + tw / 2 + 3, sy + 5))
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
        return taken

    def _draw_named_labels(self, cr, named, aw, ah, taken):
        """Street names along their street, and area names across their area.

        A STREET IS NOT A FEATURE. OSM splits a road at every junction, so
        "East Adams Street" arrives as a couple of dozen separate ways, and in a
        downtown view the median one is 69 px long — shorter than its own name,
        which is why labelling per feature put names on alleys and left every
        arterial bare. The ways that share a name are JOINED end to end first,
        the way a real map renderer does it, and the name is set on the joined
        line. Joining is exact: coordinates are quantised integers, so two ways
        meeting at a junction share a byte-identical endpoint.

        The rest are the rules a paper map follows, each one earned:

        * A name is set ALONG its line, on the longest nearly-STRAIGHT run of
          it, and never upside down. A name that follows a bend is unreadable,
          and a horizontal name near a diagonal street belongs to no street.
        * A name is only drawn where the road is long enough on screen to carry
          it, and never over a name already placed.
        * The work is BOUNDED — ranked major-roads-longest-first, and stopped at
          MAX_LABEL_CANDIDATES examined and MAX_ROAD_LABELS drawn — so a
          downtown full of named alleys costs no more than a quiet suburb."""
        if not named:
            return
        iq = 1.0 / self.pack.quant
        scale = self.scale
        scx, scy = self.cx, self.cy
        hw, hh = aw / 2.0, ah / 2.0
        log, tan, radians, degrees = math.log, math.tan, math.radians, math.degrees
        qpi = math.pi / 4

        # --- join the ways that share a name, then project once --------------
        groups = {}
        areas = []
        for f in named:
            if f[0] in AREA_LABEL_CATS:
                areas.append(f)
            else:
                groups.setdefault((f[2], f[0]), []).append(f)

        def project(pts):
            out = []
            add = out.append
            k = 0
            n = len(pts)
            while k < n:
                la = pts[k] * iq
                lo = pts[k + 1] * iq
                k += 2
                if la > 85.0:
                    la = 85.0
                elif la < -85.0:
                    la = -85.0
                my = degrees(log(tan(qpi + radians(la) / 2)))
                add(((lo - scx) * scale + hw, (scy - my) * scale + hh))
            return out

        ranked = []
        for (nm, cat), fs in groups.items():
            for pts in _join_ways(fs):
                P = project(pts)
                run = _straight_run(P)
                if run is None:
                    continue
                (ax, ay), (bx, by), length = run
                if length < 46:
                    continue
                ranked.append((0, 1 if cat == 2 else 0, -length,
                               nm, cat, ax, ay, bx, by, length))
        for f in areas:
            P = project(f[3])
            if not P:
                continue
            bx0 = min(p[0] for p in P)
            bx1 = max(p[0] for p in P)
            by0 = min(p[1] for p in P)
            by1 = max(p[1] for p in P)
            room = min(bx1 - bx0, (by1 - by0) * 3)
            if room < 46:
                continue
            ranked.append((1, 0, -room, f[2], f[0],
                           (bx0 + bx1) / 2, (by0 + by1) / 2, 0, 0, room))
        if not ranked:
            return
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        del ranked[MAX_LABEL_CANDIDATES:]

        seen = {}
        drawn = 0
        for kind, _minor, _neg, nm, cat, ax, ay, bx, by, room in ranked:
            if drawn >= MAX_ROAD_LABELS:
                break
            if kind:                            # area: name across the middle
                mx, myy = ax, ay
                ang = 0.0
            else:                               # line: name along the street
                mx, myy = (ax + bx) / 2, (ay + by) / 2
                ang = math.atan2(by - ay, bx - ax)
                # Never upside down: a street running right-to-left is labelled
                # along the same line, read the other way.
                if ang > math.pi / 2:
                    ang -= math.pi
                elif ang < -math.pi / 2:
                    ang += math.pi
            if mx < -20 or mx > aw + 20 or myy < 10 or myy > ah + 10:
                continue
            prev = seen.get(nm)
            if prev is not None and any(abs(mx - px) < 320 and abs(myy - py) < 320
                                        for px, py in prev):
                continue

            lay = _layout(cr, nm, ROAD_LABEL_SIZE.get(cat, 10))
            tw, th = lay.get_pixel_size()
            if tw + 12 > room:
                continue
            ca = abs(math.cos(ang))
            sa = abs(math.sin(ang))
            ex = tw / 2 * ca + th / 2 * sa       # the rotated box, axis-aligned
            ey = tw / 2 * sa + th / 2 * ca
            x0, y0, x1, y1 = mx - ex, myy - ey, mx + ex, myy + ey
            # Clear of the toolbar above and the © line below, and fully on.
            if x0 < 2 or y0 < 6 or x1 > aw - 2 or y1 > ah - 16:
                continue
            if any(x0 < tx1 and x1 > tx0 and y0 < ty1 and y1 > ty0
                   for tx0, ty0, tx1, ty1 in taken):
                continue
            taken.append((x0, y0, x1, y1))
            seen.setdefault(nm, []).append((mx, myy))

            cr.save()
            cr.translate(mx, myy)
            if ang:
                cr.rotate(ang)
            cr.move_to(-tw / 2, -th / 2)
            PangoCairo.layout_path(cr, lay)
            # The halo is what keeps a name readable over the road it sits on.
            cr.set_source_rgba(1, 1, 1, 0.94)
            cr.set_line_width(2.8)
            cr.set_line_join(1)
            cr.stroke_preserve()
            cr.set_source_rgb(*(LABEL_INK_AREA if kind else LABEL_INK_ROAD))
            cr.fill()
            cr.restore()
            drawn += 1

    def _invalidate(self):
        self._surface = None

    # ================= interaction =================
    def _on_press(self, w, ev):
        if ev.button == 1:
            # Direct manipulation is 1:1 with the hand. Stop a button/scroll
            # settle at its current viewport before the drag takes ownership.
            self._view_gen += 1
            if self._view_anim is not None:
                self._view_anim.cancel()
                self._view_anim = None
            self._view_moving = False
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
        if ev.direction == Gdk.ScrollDirection.UP:
            f = 1.25
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            f = 0.8
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            try:
                ok, _dx, dy = ev.get_scroll_deltas()
            except Exception:
                ok, dy = True, getattr(ev, "delta_y", 0.0)
            if not ok or not dy:
                return False
            f = 1.25 if dy < 0 else 0.8
        else:
            return False
        self._zoom(f, ev.x, ev.y)
        return True

    def _on_canvas_key(self, w, ev):
        """Pan the same viewport without requiring a drag gesture."""
        moves = {
            Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
            # Mercator y grows northward; screen y has the opposite sign.
            Gdk.KEY_Up: (0, 1), Gdk.KEY_Down: (0, -1),
        }
        move = moves.get(ev.keyval)
        if move is None or ev.state & (Gdk.ModifierType.CONTROL_MASK |
                                        Gdk.ModifierType.MOD1_MASK):
            return False
        self._view_gen += 1
        if self._view_anim is not None:
            self._view_anim.cancel()
            self._view_anim = None
        self._view_moving = False
        # An eighth of the visible map is enough to make progress while
        # retaining context. Shift deliberately makes it a half-screen jump.
        fraction = 0.5 if ev.state & Gdk.ModifierType.SHIFT_MASK else 0.125
        self.cx += move[0] * w.get_allocated_width() * fraction / self.scale
        self.cy += move[1] * w.get_allocated_height() * fraction / self.scale
        self._invalidate()
        w.queue_draw()
        self._save_cfg()
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
        cx, cy = _merc(la, lo)
        self._hi = (cx, cy)
        # A place name off the map pack, never a word of ours. Reading,
        # Bath, Nice and Bury are all catalog keys; the two-space layout inset
        # is the only thing that has been hiding it, and the inset is padding.
        nbi18n.set_verbatim(self._status, "  " + nm)
        self._animate_view(cx, cy, max(self.scale, 14000.0))

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
                ("Fit Region", self._fit),
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
