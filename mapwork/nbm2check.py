#!/usr/bin/env python3
"""Inspect / smoke-test a .nbm2 pack: header, directory, places index, search.

Usage: nbm2check.py FILE [search-term ...]
Also serves as the reference decoder for the Maps app reader.
"""
import sys
import struct
import lzma


def _rv(buf, i):                          # read unsigned varint at i -> (val, i)
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


def open_pack(path):
    f = open(path, "rb")
    assert f.read(5) == b"NBM2\n", "bad magic"
    (nlen,) = struct.unpack("<H", f.read(2))
    name = f.read(nlen).decode("utf-8", "replace")
    minlat, minlon, maxlat, maxlon = struct.unpack("<4d", f.read(32))
    (cell_deg_e6,) = struct.unpack("<i", f.read(4))
    (quant,) = struct.unpack("<I", f.read(4))
    (ncells,) = struct.unpack("<I", f.read(4))
    places_off, places_zlen, places_cnt = struct.unpack("<QII", f.read(16))
    directory = {}
    for _ in range(ncells):
        cy, cx, off, zl = struct.unpack("<iiQI", f.read(20))
        directory[(cy, cx)] = (off, zl)
    payload_base = f.tell()
    return {"f": f, "name": name, "bbox": (minlat, minlon, maxlat, maxlon),
            "cell_deg": cell_deg_e6 / 1e6, "quant": quant, "dir": directory,
            "payload_base": payload_base, "places_off": places_off,
            "places_zlen": places_zlen, "places_cnt": places_cnt}


def read_cell(p, cy, cx):
    """Return list of features in cell (cy,cx): dict(cat,flags,name,pts[(lat,lon)])."""
    loc = p["dir"].get((cy, cx))
    if not loc:
        return []
    off, zl = loc
    p["f"].seek(p["payload_base"] + off)
    raw = lzma.decompress(p["f"].read(zl))
    q = p["quant"]
    i = 0
    n, i = _rv(raw, i)
    feats = []
    for _ in range(n):
        cat = raw[i]
        flags = raw[i + 1]
        i += 2
        nl, i = _rv(raw, i)
        nm = raw[i:i + nl].decode("utf-8", "replace")
        i += nl
        npts, i = _rv(raw, i)
        pts = []
        qla = qlo = 0
        for _p in range(npts):
            dla, i = _rz(raw, i)
            dlo, i = _rz(raw, i)
            qla += dla
            qlo += dlo
            pts.append((qla / q, qlo / q))
        feats.append({"cat": cat, "flags": flags, "name": nm, "pts": pts})
    return feats


def read_places(p):
    if not p["places_cnt"]:
        return []
    p["f"].seek(p["payload_base"] + p["places_off"])
    raw = lzma.decompress(p["f"].read(p["places_zlen"]))
    i = 0
    out = []
    for _ in range(p["places_cnt"]):
        rank = raw[i]
        i += 1
        nl, i = _rv(raw, i)
        nm = raw[i:i + nl].decode("utf-8", "replace")
        i += nl
        la, lo = struct.unpack_from("<ii", raw, i)
        i += 8
        out.append((rank, nm, la / 1e6, lo / 1e6))
    return out


if __name__ == "__main__":
    p = open_pack(sys.argv[1])
    print("name       :", p["name"])
    print("bbox       : %.3f,%.3f .. %.3f,%.3f" % p["bbox"])
    print("cell_deg   :", p["cell_deg"], " cells:", len(p["dir"]),
          " quant:", p["quant"])
    print("places     :", p["places_cnt"],
          "(%.1f KB compressed)" % (p["places_zlen"] / 1024))
    places = read_places(p)
    rc = {}
    for rank, nm, la, lo in places:
        rc[rank] = rc.get(rank, 0) + 1
    print("place ranks:", dict(sorted(rc.items())),
          "(1=city 2=town 3=village 4=hamlet 5=suburb)")
    # sample a populated cell
    if p["dir"]:
        k = next(iter(p["dir"]))
        feats = read_cell(p, *k)
        cats = {}
        for f in feats:
            cats[f["cat"]] = cats.get(f["cat"], 0) + 1
        print("cell", k, "->", len(feats), "features, cats", dict(sorted(cats.items())))
        lbl = [f["name"] for f in feats if f["cat"] == 11][:5]
        print("  labels in that cell:", lbl)
    for term in sys.argv[2:]:
        t = term.lower()
        hits = [(rank, nm, la, lo) for rank, nm, la, lo in places
                if t in nm.lower()]
        hits.sort()
        print("search %-14r -> %d hits; top:" % (term, len(hits)),
              [(h[1], round(h[2], 3), round(h[3], 3)) for h in hits[:4]])
