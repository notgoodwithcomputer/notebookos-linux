#!/usr/bin/env python3
"""Off-guest cairo renderer for .nbm2 packs -> PNG, to validate the streaming
draw logic (cell culling, zoom LOD, two-pass roads, decluttered labels) before
porting it into de/maps.py. Usage: maprender_test.py PACK.nbm2
"""
import sys
import math
import cairo
import nbm2check as nb

STYLE = {
    7:  ("fill", (0.80, 0.88, 0.72), 0, None),               # green
    8:  ("fill", (0.93, 0.91, 0.87), 0, None),               # landuse
    4:  ("fill", (0.66, 0.82, 0.90), 0, (0.52, 0.70, 0.82)),  # water
    5:  ("line", (0.60, 0.78, 0.88), 1.6, None),             # waterway
    9:  ("line", (0.55, 0.53, 0.50), 1.4, None),             # rail
    10: ("line", (0.55, 0.72, 0.83), 1.0, None),             # coastline
    3:  ("line", (0.72, 0.64, 0.54), 1.1, None),             # path
    2:  ("line", (1.00, 1.00, 0.98), 3.0, (0.76, 0.73, 0.67)),  # minor road
    1:  ("line", (0.99, 0.85, 0.52), 5.0, (0.79, 0.60, 0.30)),  # major road
    0:  ("line", (0.86, 0.84, 0.80), 1.4, None),             # other road
}
LAND = (0.96, 0.95, 0.92)
# scale (px per mercator-degree) below which a category is hidden. Residential
# streets and paths only appear at street zoom; regional views show the
# highway/water/green skeleton so the town doesn't drown in white.
MINZOOM = {1: 0, 4: 0, 5: 0, 9: 0, 10: 0, 7: 0, 8: 2500, 2: 7000, 3: 18000, 0: 8000}
# label min-scale by place rank (1 city .. 5 suburb)
LABEL_MINZOOM = {1: 0, 2: 1400, 3: 6000, 4: 16000, 5: 9000}
LABEL_SIZE = {1: 15, 2: 13, 3: 11, 4: 10, 5: 11}


def merc(lat, lon):
    lat = max(-85.0, min(85.0, lat))
    return lon, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def merc_y_to_lat(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def render(pack, clat, clon, scale, w, h, out):
    cx, cy = merc(clat, clon)                 # center in mercator
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*LAND)
    cr.paint()

    def to_screen(mx, my):
        return ((mx - cx) * scale + w / 2, (cy - my) * scale + h / 2)

    # view bounds -> lat/lon -> cell range (+2 margin for way spillover)
    lon0 = cx - (w / 2) / scale
    lon1 = cx + (w / 2) / scale
    myt = cy + (h / 2) / scale
    myb = cy - (h / 2) / scale
    latt = merc_y_to_lat(myt)
    latb = merc_y_to_lat(myb)
    cd = pack["cell_deg"]
    m = 2
    cy0 = int(math.floor(min(latb, latt) / cd)) - m
    cy1 = int(math.floor(max(latb, latt) / cd)) + m
    cx0 = int(math.floor(lon0 / cd)) - m
    cx1 = int(math.floor(lon1 / cd)) + m
    ncells = (cy1 - cy0 + 1) * (cx1 - cx0 + 1)

    feats = []
    decoded = 0
    for ccy in range(cy0, cy1 + 1):
        for ccx in range(cx0, cx1 + 1):
            if (ccy, ccx) in pack["dir"]:
                feats.extend(nb.read_cell(pack, ccy, ccx))
                decoded += 1
    # cull to view + convert to mercator screen paths lazily
    vminx, vmaxx = lon0, lon1
    vminy, vmaxy = myb, myt

    def build(cr, pts):
        cr.new_path()
        first = True
        for la, lo in pts:
            mx, my = merc(la, lo)
            sx, sy = to_screen(mx, my)
            if first:
                cr.move_to(sx, sy)
                first = False
            else:
                cr.line_to(sx, sy)

    def fbbox(pts):
        xs = [p[1] for p in pts]
        ys = [merc(p[0], 0)[1] for p in pts]
        return min(xs), max(xs), min(ys), max(ys)

    areas, lines, roads, labels = [], [], [], []
    for f in feats:
        cat = f["cat"]
        if cat == 11:
            if scale >= LABEL_MINZOOM.get(f["flags"], 1e9):
                labels.append(f)
            continue
        if scale < MINZOOM.get(cat, 0):
            continue
        x0, x1, y0, y1 = fbbox(f["pts"])
        if x1 < vminx or x0 > vmaxx or y1 < vminy or y0 > vmaxy:
            continue
        st = STYLE.get(cat, STYLE[0])
        if st[0] == "fill":
            areas.append(f)
        elif cat in (1, 2):
            roads.append(f)
        else:
            lines.append(f)

    rw = lambda base: max(0.6, base * max(0.35, min(1.5, scale / 20000.0)))
    # areas
    for f in areas:
        st = STYLE[f["cat"]]
        build(cr, f["pts"])
        cr.close_path()
        cr.set_source_rgb(*st[1])
        cr.fill_preserve()
        if st[3]:
            cr.set_source_rgb(*st[3])
            cr.set_line_width(0.6)
            cr.stroke()
        else:
            cr.new_path()
    # non-road lines
    for f in lines:
        st = STYLE[f["cat"]]
        build(cr, f["pts"])
        cr.set_line_join(1)
        cr.set_line_cap(1)
        cr.set_source_rgb(*st[1])
        cr.set_line_width(max(0.7, st[2]))
        cr.stroke()
    # roads: all casings, then all fills (so the network reads as one connected
    # mesh, not beaded). Thin roads at low zoom skip the casing pass.
    for casing in (True, False):
        for f in roads:
            st = STYLE[f["cat"]]
            lw = rw(st[2])
            if casing and (lw < 2.2 or not st[3]):
                continue
            build(cr, f["pts"])
            cr.set_line_join(1)
            cr.set_line_cap(1)
            if casing:
                cr.set_source_rgb(*st[3])
                cr.set_line_width(lw + 1.4)
            else:
                cr.set_source_rgb(*st[1])
                cr.set_line_width(lw)
            cr.stroke()
    # labels: declutter greedily by rank
    labels.sort(key=lambda f: f["flags"])
    placed = []
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    for f in labels:
        la, lo = f["pts"][0]
        mx, my = merc(la, lo)
        sx, sy = to_screen(mx, my)
        if sx < -40 or sx > w + 40 or sy < -20 or sy > h + 20:
            continue
        if any(abs(sx - px) < 60 and abs(sy - py) < 16 for px, py in placed):
            continue
        placed.append((sx, sy))
        cr.set_font_size(LABEL_SIZE.get(f["flags"], 11))
        te = cr.text_extents(f["name"])
        tx = sx - te.width / 2
        ty = sy + te.height / 2
        cr.move_to(tx, ty)
        cr.text_path(f["name"])
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(2.6)
        cr.set_line_join(1)
        cr.stroke_preserve()
        cr.set_source_rgb(0.12, 0.11, 0.10)
        cr.fill()
        # a small dot at the place point
        cr.arc(sx, sy, 1.6, 0, 2 * math.pi)
        cr.set_source_rgb(0.30, 0.28, 0.25)
        cr.fill()
    surf.write_to_png(out)
    print("  %-22s scale=%-7g cells=%d(decoded %d) feats=%d labels=%d -> %s"
          % ("(%.3f,%.3f)" % (clat, clon), scale, ncells, decoded,
             len(areas) + len(lines) + len(roads), len(placed), out))


if __name__ == "__main__":
    pack = nb.open_pack(sys.argv[1])
    print("pack:", pack["name"], "cells", len(pack["dir"]), "places", pack["places_cnt"])
    places = nb.read_places(pack)
    byrank = sorted(places)
    # pick a city center if present
    city = next((p for p in byrank if p[0] == 1), byrank[0] if byrank else None)
    if city:
        _, nm, la, lo = city
        print("centering on:", nm, la, lo)
        render(pack, la, lo, 22000, 900, 640, "view_city.png")   # metro
        render(pack, la, lo, 6000, 900, 640, "view_region.png")  # region
        render(pack, la, lo, 900, 900, 640, "view_wide.png")     # wide
