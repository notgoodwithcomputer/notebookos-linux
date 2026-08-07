#!/usr/bin/env python3
"""osm2nbmap2 — Tier-2 (.nbm2) encoder: streets, no buildings, small.

Reads an .osm.pbf (via nbpbf, stdlib only) and writes a grid-tiled, simplified,
delta-varint, lzma-compressed map pack the rewritten Maps app streams by cell.

Pipeline: classify + DROP buildings/addresses → Douglas-Peucker simplify at the
target pixel resolution → quantize coords to a cell-relative grid → per-cell
delta+zigzag varint geometry → lzma per cell. Cells the viewport doesn't touch
are never decoded, so total size is decoupled from memory/render cost.

.nbm2 layout (little-endian):
  magic "NBM2\\n" | uint16 name len + name
  double min_lat min_lon max_lat max_lon         (data bbox)
  int32  cell_deg_e6                              (grid cell size, microdeg)
  uint32 quant                                    (coord quantum, e.g. 100000 = 1e5)
  uint32 cell_count
  directory: cell_count × (int32 cy, int32 cx, uint64 offset, uint32 zlen)
  payload:  concatenated lzma (xz) frames, each = one cell's features:
      varint feature_count
      per feature: uint8 cat, uint8 flags, varint name_len, name bytes,
                   varint npts, then npts × (zigzag-varint dqx, zigzag-varint dqy)
                   where coords are cell-relative quantized ints, delta-coded.
"""
import sys
import os
import struct
import math

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import nbpbf  # noqa: E402
import lzma  # noqa: E402   (stdlib; guest needs BR2_PACKAGE_PYTHON3_XZ)

# ---- Tier-2 classification: keep the street/water/land/rail network; NO buildings
CAT = {"road_major": 1, "road_minor": 2, "path": 3, "water": 4, "waterway": 5,
       "green": 7, "landuse": 8, "rail": 9, "coast": 10}
MAJOR = {"motorway", "trunk", "primary", "secondary", "motorway_link",
         "trunk_link", "primary_link", "secondary_link", "tertiary",
         "tertiary_link"}
MINOR = {"residential", "unclassified", "living_street", "service", "road"}
PATHS = {"footway", "path", "pedestrian", "steps", "cycleway", "track"}
# NB_SKIP_PATHS=1 drops the whole path category at classify time — both the
# node-marking pass and the geometry pass inherit it, so the store shrinks too.
# The continent cut ships without footpaths so pack+ISO fit a 4 GB stick.
SKIP_PATHS = bool(os.environ.get("NB_SKIP_PATHS"))
GREEN = {"grass", "forest", "meadow", "recreation_ground", "village_green",
         "cemetery", "orchard", "farmland", "farmyard", "park", "garden",
         "nature_reserve", "wood", "scrub", "heath"}
LANDUSE = {"residential", "commercial", "industrial", "retail"}


def classify(t):
    """Return (category, is_area) or None to drop. Buildings/POIs dropped."""
    if b"building" in t:
        return None
    hw = t.get(b"highway", b"").decode()
    if hw:
        if hw in MAJOR:
            return "road_major", False
        if hw in MINOR:
            return "road_minor", False
        if hw in PATHS:
            return None if SKIP_PATHS else ("path", False)
        return "road_minor", False
    nat = t.get(b"natural", b"").decode()
    if nat == "water":
        return "water", True
    if nat == "coastline":
        return "coast", False
    if nat in ("wood", "scrub", "heath", "grassland"):
        return "green", True
    wat = t.get(b"waterway", b"").decode()
    if wat in ("river", "stream", "canal", "riverbank"):
        return "waterway", False
    if t.get(b"water"):
        return "water", True
    lu = t.get(b"landuse", b"").decode()
    if lu in GREEN:
        return "green", True
    if lu in LANDUSE:
        return "landuse", True
    leis = t.get(b"leisure", b"").decode()
    if leis in ("park", "garden", "nature_reserve", "pitch", "golf_course"):
        return "green", True
    return None


def simplify(pts, eps):
    """Douglas-Peucker on (lat,lon) points; eps in degrees."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        fmax = 0.0
        idx = -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if d2 == 0:
                dist = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / d2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                cx, cy = ax + t * dx, ay + t * dy
                dist = (px - cx) ** 2 + (py - cy) ** 2
            if dist > fmax:
                fmax = dist
                idx = i
        if idx != -1 and fmax > eps * eps:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [pts[i] for i in range(len(pts)) if keep[i]]


def encode(src, dst, name="Map", cell_deg=0.25, quant=100000, eps_px=1.2):
    # eps in degrees ≈ pixels at ~z15: 1 quantum = 1/quant deg; eps_px quanta
    eps = eps_px / quant
    nodes = {}
    cells = {}           # (cy,cx) -> list of (cat, flags, name, [ (qlat,qlon) ])
    minla = minlo = 1e9
    maxla = maxlo = -1e9
    nfeat = 0
    for e in nbpbf.elements(src):
        if e[0] == "node":
            nodes[e[1]] = (e[2], e[3])
        else:
            _, wid, tags, refs = e
            cls = classify(tags)
            if cls is None or len(refs) < 2:
                continue
            cat, is_area = cls
            pts = [nodes[r] for r in refs if r in nodes]
            if len(pts) < 2:
                continue
            pts = simplify(pts, eps)
            if len(pts) < 2:
                continue
            nm = tags.get(b"name", b"")[:80]
            # assign to the cell of the first point
            la0, lo0 = pts[0]
            cy = int(math.floor(la0 / cell_deg))
            cx = int(math.floor(lo0 / cell_deg))
            q = []
            for la, lo in pts:
                q.append((int(round(la * quant)), int(round(lo * quant))))
                if la < minla:
                    minla = la
                if la > maxla:
                    maxla = la
                if lo < minlo:
                    minlo = lo
                if lo > maxlo:
                    maxlo = lo
            flags = 1 if (is_area and refs[0] == refs[-1]) else 0
            cells.setdefault((cy, cx), []).append((CAT[cat], flags, nm, q))
            nfeat += 1
    # ---- serialize
    def wv(buf, n):                       # unsigned varint
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                buf.append(b | 0x80)
            else:
                buf.append(b)
                return

    def wz(buf, n):                       # zigzag varint
        wv(buf, (n << 1) ^ (n >> 63) if n >= 0 else ((-n) << 1) - 1)

    directory = []
    payload = bytearray()
    for (cy, cx), feats in cells.items():
        body = bytearray()
        wv(body, len(feats))
        for cat, flags, nm, q in feats:
            body.append(cat)
            body.append(flags)
            wv(body, len(nm))
            body += nm
            wv(body, len(q))
            plat = plon = 0
            for qla, qlo in q:
                dla = qla - plat
                dlo = qlo - plon
                plat, plon = qla, qlo
                wz(body, dla)
                wz(body, dlo)
        comp = lzma.compress(bytes(body), preset=9)
        directory.append((cy, cx, len(payload), len(comp)))
        payload += comp
    with open(dst, "wb") as f:
        f.write(b"NBM2\n")
        nb = name.encode()[:65535]
        f.write(struct.pack("<H", len(nb)))
        f.write(nb)
        f.write(struct.pack("<4d", minla, minlo, maxla, maxlo))
        f.write(struct.pack("<i", int(cell_deg * 1e6)))
        f.write(struct.pack("<I", quant))
        f.write(struct.pack("<I", len(directory)))
        for cy, cx, off, zl in directory:
            f.write(struct.pack("<iiQI", cy, cx, off, zl))
        f.write(payload)
    return nfeat, len(cells), (minla, minlo, maxla, maxlo)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "Map"
    import os
    import time
    t0 = time.time()
    nf, nc, bbox = encode(src, dst, name)
    sz = os.path.getsize(dst)
    dla = (bbox[2] - bbox[0]) * 111.0
    dlo = (bbox[3] - bbox[1]) * 111.0 * math.cos(math.radians((bbox[0] + bbox[2]) / 2))
    km2 = max(1.0, abs(dla * dlo))
    print("%s: %d features, %d cells, %.2f MB  (%.1f KB/km^2)  in %.0fs"
          % (dst, nf, nc, sz / 1048576, sz / 1024 / km2, time.time() - t0))
