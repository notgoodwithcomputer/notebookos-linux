#!/usr/bin/env python3
"""osm2nbmap3 — memory-scalable Tier-2 (.nbm2) encoder for a whole continent.

Byte-for-byte the same .nbm2 output as osm2nbmap2, but it never holds all node
coordinates in RAM (osm2nbmap2's node dict would need >100 GB for North
America). It exploits that a PBF stores nodes in ascending-id order and makes
three streaming passes over the .osm.pbf:

  Pass A (ways):  classify Tier-2; set a bit for every referenced node id.
  Pass B (nodes): nodes arrive in ascending id; for each marked id APPEND
                  (id, lat_e6, lon_e6) -> the id array is already sorted.
  Pass C (ways):  re-classify; resolve each ref by bisect into the id array;
                  simplify, quantize, bucket into grid cells.
  Finalize:       lzma each cell, write directory + payload.

Peak RAM ~= bitset (id_max/8, ~1.4 GB) + 16 bytes x kept-node-count. For North
America that is ~1.4 GB + ~13-20 GB, which fits in 31 GB.
"""
import sys
import os
import struct
import math
import time
import bisect
import mmap
import pickle
from array import array

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import nbpbf  # noqa: E402
from osm2nbmap2 import classify, simplify, CAT  # noqa: E402
import lzma  # noqa: E402   (stdlib; guest needs BR2_PACKAGE_PYTHON3_XZ)

E6 = 1000000
LABEL = 11                                # single-point place-label feature
# place=* label nodes we keep (rank drives label size / search priority).
# neighbourhood/locality are dropped: too numerous continent-wide for the value
# at a street-level zoom, and they would bloat the search index.
PLACE_RANK = {b"city": 1, b"town": 2, b"village": 3, b"hamlet": 4,
              b"suburb": 5}


def _wv(buf, n):                          # unsigned varint
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def _wz(buf, n):                          # zigzag varint
    _wv(buf, (n << 1) if n >= 0 else (((-n) << 1) - 1))


def _sparse_from_store(store_path, stride):
    """Rebuild the every-`stride`-th-id sparse index by scanning a store that a
    prior passB already wrote (records are 16 bytes, sorted by id)."""
    sparse = array("q")
    up = struct.Struct("<q").unpack_from
    with open(store_path, "rb") as f:
        g = 0
        blk = stride * 16 * 256
        while True:
            block = f.read(blk)
            if not block:
                break
            recs = len(block) // 16
            k = (-g) % stride                 # first strided record in this block
            while k < recs:
                sparse.append(up(block, k * 16)[0])
                k += stride
            g += recs
    return sparse


def _scan_places(src, cache):
    """Extract place=* label nodes from the pbf (cached to `cache` so a resumed
    run scans them only once)."""
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return pickle.load(f)
    places = []
    for _, nid, la, lo, tags in nbpbf.elements(src, want_ways=False,
                                               want_node_tags=True):
        if tags:
            rank = PLACE_RANK.get(tags.get(b"place"))
            if rank:
                nm = tags.get(b"name")
                if nm:
                    places.append((la, lo, nm[:80], rank))
    with open(cache, "wb") as f:
        pickle.dump(places, f)
    return places


def encode(src, dst, name="Map", cell_deg=0.1, quant=100000, eps_px=1.2,
           id_max=20_000_000_000, verbose=True, reuse_store=False):
    t0 = time.time()
    eps = eps_px / quant
    # store path + sparse stride are shared by the build and resume paths
    store_path = dst + ".nodes.tmp"
    STRIDE = 1024

    if reuse_store and os.path.exists(store_path) and \
            os.path.getsize(store_path) >= 16:
        # ---- Resume: a prior passA+passB already wrote the COMPLETE store ----
        # (Salvages hours of parsing after a kill; run detached to avoid caps.)
        ncount = os.path.getsize(store_path) // 16
        sparse = _sparse_from_store(store_path, STRIDE)
        places = _scan_places(src, dst + ".places.pkl")
        if verbose:
            print("  resume: %d nodes in store, %d sparse, %d places  %.0fs"
                  % (ncount, len(sparse), len(places), time.time() - t0),
                  flush=True)
    else:
        # ---- Pass A: mark referenced nodes of kept Tier-2 ways --------------
        # (OSM node ids ~12e9 in 2026 and climbing; size generously + guard so a
        # single stray ref can never crash the multi-hour continent encode.)
        need = bytearray((id_max >> 3) + 1)
        kept_ways = 0
        for _, wid, tags, refs in nbpbf.elements(src, want_nodes=False):
            if len(refs) < 2 or classify(tags) is None:
                continue
            kept_ways += 1
            for r in refs:
                if 0 <= r < id_max:
                    need[r >> 3] |= 1 << (r & 7)
        if verbose:
            print("  passA: %d kept ways  %.0fs" % (kept_ways, time.time() - t0),
                  flush=True)

        # ---- Pass B: write marked-node coords to a sorted on-disk store ------
        # PBF nodes are id-ascending, so appending (id,lat,lon) gives a store
        # already sorted by id; passC looks them up by mmap + sparse index, so
        # RSS stays ~ the bitset not ~20 GB of arrays for a whole continent.
        sparse = array("q")               # id of every STRIDE-th stored node
        places = []                       # (lat, lon, name_bytes, rank)
        ncount = 0
        prev = -1
        sorted_ok = True
        rec = struct.Struct("<qii").pack
        wbuf = bytearray()
        sf = open(store_path, "wb")
        try:
            for _, nid, la, lo, tags in nbpbf.elements(src, want_ways=False,
                                                       want_node_tags=True):
                if 0 <= nid < id_max and need[nid >> 3] & (1 << (nid & 7)):
                    if nid < prev:
                        sorted_ok = False
                    prev = nid
                    if ncount % STRIDE == 0:
                        sparse.append(nid)
                    wbuf += rec(nid, int(round(la * E6)), int(round(lo * E6)))
                    ncount += 1
                    if len(wbuf) >= (1 << 20):
                        sf.write(wbuf)
                        del wbuf[:]
                if tags:
                    rank = PLACE_RANK.get(tags.get(b"place"))
                    if rank:
                        nm = tags.get(b"name")
                        if nm:
                            places.append((la, lo, nm[:80], rank))
            if wbuf:
                sf.write(wbuf)
        finally:
            sf.close()
        del need
        if not sorted_ok:
            raise RuntimeError("node store not id-sorted; expected a sorted PBF")
        if verbose:
            print("  passB: %d nodes -> store (%.2f GB), %d place labels  %.0fs"
                  % (ncount, ncount * 16 / 1e9, len(places), time.time() - t0),
                  flush=True)

    # ---- Pass C: resolve geometry via the mmap'd node store + sparse index --
    # Serialize each feature to packed bytes immediately (holding NA's ~66M
    # features as Python objects would add ~23 GB; as delta-varint bytes ~2 GB).
    sf = open(store_path, "rb")
    mm = mmap.mmap(sf.fileno(), 0, access=mmap.ACCESS_READ)
    id_at = struct.Struct("<q").unpack_from
    rec_at = struct.Struct("<qii").unpack_from
    br = bisect.bisect_right

    def lookup(r):
        j = br(sparse, r) - 1             # narrow to a window via sparse index
        if j < 0:
            return None
        a = j * STRIDE
        b = a + STRIDE
        if b > ncount:
            b = ncount
        while a < b:                      # binary search within the window
            mid = (a + b) >> 1
            if id_at(mm, mid * 16)[0] < r:
                a = mid + 1
            else:
                b = mid
        if a < ncount:
            rid, rla, rlo = rec_at(mm, a * 16)
            if rid == r:
                return (rla / E6, rlo / E6)
        return None

    cellbuf = {}          # (cy,cx) -> bytearray of serialized features
    cellcnt = {}          # (cy,cx) -> feature count (prepended at compress time)
    minla = minlo = 1e9
    maxla = maxlo = -1e9
    nfeat = 0
    for _, wid, tags, refs in nbpbf.elements(src, want_nodes=False):
        if len(refs) < 2:
            continue
        cls = classify(tags)
        if cls is None:
            continue
        cat, is_area = cls
        pts = []
        for r in refs:
            c = lookup(r)
            if c is not None:
                pts.append(c)
        if len(pts) < 2:
            continue
        pts = simplify(pts, eps)
        if len(pts) < 2:
            continue
        nm = tags.get(b"name", b"")[:80]
        la0, lo0 = pts[0]
        key = (int(math.floor(la0 / cell_deg)), int(math.floor(lo0 / cell_deg)))
        buf = cellbuf.get(key)
        if buf is None:
            buf = bytearray()
            cellbuf[key] = buf
            cellcnt[key] = 0
        buf.append(CAT[cat])
        buf.append(1 if (is_area and refs[0] == refs[-1]) else 0)
        _wv(buf, len(nm))
        buf += nm
        _wv(buf, len(pts))
        plat = plon = 0
        for la, lo in pts:
            qla = int(round(la * quant))
            qlo = int(round(lo * quant))
            _wz(buf, qla - plat)
            _wz(buf, qlo - plon)
            plat, plon = qla, qlo
            if la < minla:
                minla = la
            if la > maxla:
                maxla = la
            if lo < minlo:
                minlo = lo
            if lo > maxlo:
                maxlo = lo
        cellcnt[key] += 1
        nfeat += 1
    mm.close()
    sf.close()
    if not os.environ.get("NB_KEEP_STORE"):   # keep for resume-testing
        try:
            os.remove(store_path)
        except OSError:
            pass
    if verbose:
        print("  passC: %d features in %d cells  %.0fs"
              % (nfeat, len(cellbuf), time.time() - t0), flush=True)

    # ---- add place labels as single-point (cat 11) features to their cells --
    for la, lo, nm, rank in places:
        key = (int(math.floor(la / cell_deg)), int(math.floor(lo / cell_deg)))
        buf = cellbuf.get(key)
        if buf is None:
            buf = bytearray()
            cellbuf[key] = buf
            cellcnt[key] = 0
        buf.append(LABEL)
        buf.append(rank)
        _wv(buf, len(nm))
        buf += nm
        _wv(buf, 1)
        _wz(buf, int(round(la * quant)))
        _wz(buf, int(round(lo * quant)))
        cellcnt[key] += 1
        if la < minla:
            minla = la
        if la > maxla:
            maxla = la
        if lo < minlo:
            minlo = lo
        if lo > maxlo:
            maxlo = lo

    # ---- compress each cell (count varint + feature bytes) via lzma ---------
    directory = []
    payload = bytearray()
    for key, buf in cellbuf.items():
        body = bytearray()
        _wv(body, cellcnt[key])
        body += buf
        comp = lzma.compress(bytes(body), preset=9)
        directory.append((key[0], key[1], len(payload), len(comp)))
        payload += comp

    # ---- places index (fast search / low-zoom labels): rank, name, point ----
    pblob = bytearray()
    for la, lo, nm, rank in places:
        pblob.append(rank)
        _wv(pblob, len(nm))
        pblob += nm
        pblob += struct.pack("<ii", int(round(la * E6)), int(round(lo * E6)))
    places_comp = lzma.compress(bytes(pblob), preset=9) if places else b""

    with open(dst, "wb") as f:
        f.write(b"NBM2\n")
        nb = name.encode()[:65535]
        f.write(struct.pack("<H", len(nb)))
        f.write(nb)
        f.write(struct.pack("<4d", minla, minlo, maxla, maxlo))
        f.write(struct.pack("<i", int(cell_deg * 1e6)))
        f.write(struct.pack("<I", quant))
        f.write(struct.pack("<I", len(directory)))
        # places index: offset (from payload start), compressed len, entry count
        f.write(struct.pack("<QII", len(payload), len(places_comp), len(places)))
        for cy, cx, off, zl in directory:
            f.write(struct.pack("<iiQI", cy, cx, off, zl))
        f.write(payload)
        f.write(places_comp)
    return nfeat, len(cellbuf), (minla, minlo, maxla, maxlo)


if __name__ == "__main__":
    import resource
    src, dst = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "Map"
    cell_deg = float(sys.argv[4]) if len(sys.argv) > 4 else 0.1
    reuse = "resume" in sys.argv[5:]      # reuse an existing complete node store
    t0 = time.time()
    nf, nc, bbox = encode(src, dst, name, cell_deg=cell_deg, reuse_store=reuse)
    sz = os.path.getsize(dst)
    dla = (bbox[2] - bbox[0]) * 111.0
    dlo = (bbox[3] - bbox[1]) * 111.0 * math.cos(math.radians((bbox[0] + bbox[2]) / 2))
    km2 = max(1.0, abs(dla * dlo))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0
    print("%s: %d features, %d cells, %.2f MB (%.1f KB/km^2)  %.0fs  peakRSS %.2f GB"
          % (dst, nf, nc, sz / 1048576, sz / 1024 / km2, time.time() - t0, rss))
