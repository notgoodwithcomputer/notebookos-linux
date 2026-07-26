#!/usr/bin/env python3
"""Minimal, dependency-free OpenStreetMap .osm.pbf reader (stdlib only).

Yields ("node", id, lat, lon) and ("way", id, tags:dict[bytes,bytes], refs:[int]).
Enough to build the Maps .nbmap pack: nodes give coordinates, ways give the
geometry + tags we classify/filter. Relations are skipped (not needed for the
road/water/landuse/rail layers). Decodes the PBF wire format by hand:
BlobHeader/Blob framing, zlib blobs, PrimitiveBlock → DenseNodes / Way, with
delta+zigzag varints. See wiki.openstreetmap.org/wiki/PBF_Format.
"""
import struct
import zlib


def _varints(buf):
    """Yield the varints packed into a length-delimited field body."""
    i = 0
    n = len(buf)
    while i < n:
        shift = 0
        val = 0
        while True:
            b = buf[i]
            i += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        yield val


def _fields(buf):
    """Yield (field_number, wire_type, value) for a protobuf message body.
    value is an int for varint/fixed, or a bytes slice for length-delimited."""
    i = 0
    n = len(buf)
    while i < n:
        shift = 0
        key = 0
        while True:
            b = buf[i]
            i += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        fn = key >> 3
        wt = key & 7
        if wt == 0:                       # varint
            shift = 0
            val = 0
            while True:
                b = buf[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield fn, wt, val
        elif wt == 2:                     # length-delimited
            shift = 0
            ln = 0
            while True:
                b = buf[i]
                i += 1
                ln |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield fn, wt, buf[i:i + ln]
            i += ln
        elif wt == 1:                     # 64-bit
            yield fn, wt, buf[i:i + 8]
            i += 8
        elif wt == 5:                     # 32-bit
            yield fn, wt, buf[i:i + 4]
            i += 4
        else:
            raise ValueError("bad wire type %d at %d" % (wt, i))


def _zz(n):
    """zigzag decode (protobuf sint64)."""
    return (n >> 1) ^ -(n & 1)


def _i64(v):
    """interpret a plain varint as signed int64."""
    return v - (1 << 64) if v >= (1 << 63) else v


def _blocks(path):
    """Yield (blob_type:str, message_bytes) for each fileblock."""
    with open(path, "rb") as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                return
            (hlen,) = struct.unpack(">I", hdr)
            bh = f.read(hlen)
            btype = None
            dsize = 0
            for fn, wt, v in _fields(bh):
                if fn == 1 and wt == 2:
                    btype = v.decode("ascii", "replace")
                elif fn == 3 and wt == 0:
                    dsize = v
            blob = f.read(dsize)
            if len(blob) < dsize:                  # truncated download tail
                return
            raw = None
            try:
                for fn, wt, v in _fields(blob):
                    if fn == 1 and wt == 2:        # uncompressed
                        raw = bytes(v)
                    elif fn == 3 and wt == 2:      # zlib_data
                        raw = zlib.decompress(v)
                    # fields 4/6/7 (lzma/lz4/zstd) unsupported; Geofabrik uses zlib
            except (zlib.error, IndexError):
                return                             # stop cleanly on a bad tail
            if raw is not None:
                yield btype, raw


def elements(path, want_nodes=True, want_ways=True, want_node_tags=False):
    """Stream ('node', id, lat, lon) then ('way', id, tags, refs).
    With want_node_tags, node tuples are ('node', id, lat, lon, tags) so the
    caller can pick out place=* label nodes. (PBF orders all nodes before ways
    within the file, as OSM does.)"""
    for btype, raw in _blocks(path):
        if btype != "OSMData":
            continue
        strings = [b""]
        groups = []
        gran = 100
        lat_off = 0
        lon_off = 0
        for fn, wt, v in _fields(raw):
            if fn == 1 and wt == 2:               # StringTable
                strings = [s for f2, w2, s in _fields(v) if f2 == 1]
            elif fn == 2 and wt == 2:             # PrimitiveGroup
                groups.append(v)
            elif fn == 17 and wt == 0:
                gran = v
            elif fn == 19 and wt == 0:
                lat_off = _i64(v)
            elif fn == 20 and wt == 0:
                lon_off = _i64(v)
        scale = gran
        for g in groups:
            for fn, wt, v in _fields(g):
                if fn == 2 and wt == 2 and want_nodes:      # DenseNodes
                    ids = lats = lons = None
                    kvit = None
                    for f2, w2, v2 in _fields(v):
                        if f2 == 1:
                            ids = list(_varints(v2))
                        elif f2 == 8:
                            lats = list(_varints(v2))
                        elif f2 == 9:
                            lons = list(_varints(v2))
                        elif f2 == 10 and want_node_tags:
                            kvit = _varints(v2)       # key,val,...,0 per node
                    if not ids:
                        continue
                    idc = latc = lonc = 0
                    for k in range(len(ids)):
                        idc += _zz(ids[k])
                        latc += _zz(lats[k])
                        lonc += _zz(lons[k])
                        la = (lat_off + scale * latc) * 1e-9
                        lo = (lon_off + scale * lonc) * 1e-9
                        if want_node_tags:
                            tags = {}
                            if kvit is not None:
                                for kk in kvit:       # consumes up to this
                                    if kk == 0:       # node's 0 terminator
                                        break
                                    tags[strings[kk]] = strings[next(kvit)]
                            yield ("node", idc, la, lo, tags)
                        else:
                            yield ("node", idc, la, lo)
                elif fn == 3 and wt == 2 and want_ways:     # Way
                    wid = None
                    keys = vals = refs = None
                    for f2, w2, v2 in _fields(v):
                        if f2 == 1:
                            wid = v2
                        elif f2 == 2:
                            keys = list(_varints(v2))
                        elif f2 == 3:
                            vals = list(_varints(v2))
                        elif f2 == 8:
                            refs = list(_varints(v2))
                    tags = {}
                    if keys and vals:
                        for i in range(len(keys)):
                            tags[strings[keys[i]]] = strings[vals[i]]
                    nodes = []
                    if refs:
                        r = 0
                        for x in refs:
                            r += _zz(x)
                            nodes.append(r)
                    yield ("way", wid, tags, nodes)


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    nn = nw = 0
    sample_lat = sample_lon = None
    sample_way = None
    for e in elements(src):
        if e[0] == "node":
            nn += 1
            if sample_lat is None:
                sample_lat, sample_lon = e[2], e[3]
        else:
            nw += 1
            if sample_way is None and e[2].get(b"highway"):
                sample_way = (e[1], {k.decode(): v.decode() for k, v in e[2].items()}, len(e[3]))
    print("nodes=%d ways=%d" % (nn, nw))
    print("first node coord: %.5f, %.5f" % (sample_lat, sample_lon))
    print("sample highway way:", sample_way)
