#!/usr/bin/env python3
"""Parallel + resumable pass C for a continent pack.

Reuses the COMPLETE on-disk node store an osm2nbmap3 passA+passB already wrote,
then resolves geometry across N worker processes (each mmaps the shared store and
handles every N-th way), and merges the shards + place labels into the final
.nbm2. ~N× faster than serial pass C, and resumable: places are cached, and each
worker writes its shard atomically, so a killed run is continued by re-running —
finished shards are skipped and only the merge/leftover workers redo.

  na_parallel.py PBF STORE DST NAME CELL_DEG [NWORKERS]
  na_parallel.py --worker PBF STORE SPARSE WID N CELL_DEG QUANT EPS OUT
"""
import sys
import os
import struct
import math
import time
import bisect
import mmap
import lzma
import pickle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbpbf  # noqa: E402
from osm2nbmap3 import (classify, simplify, CAT, LABEL, E6,  # noqa: E402
                        _wv, _wz, _sparse_from_store, _scan_places)

STRIDE = 1024


def _emit_way(tags, refs, lookup, eps, quant, cell_deg, cellbuf, cellcnt, bb):
    """Resolve one way's geometry and serialize it into its cell (mirrors the
    serial pass C in osm2nbmap3). Returns 1 if a feature was emitted."""
    if len(refs) < 2:
        return 0
    cls = classify(tags)
    if cls is None:
        return 0
    cat, is_area = cls
    pts = []
    for r in refs:
        c = lookup(r)
        if c is not None:
            pts.append(c)
    if len(pts) < 2:
        return 0
    pts = simplify(pts, eps)
    if len(pts) < 2:
        return 0
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
        if la < bb[0]:
            bb[0] = la
        if la > bb[2]:
            bb[2] = la
        if lo < bb[1]:
            bb[1] = lo
        if lo > bb[3]:
            bb[3] = lo
    cellcnt[key] += 1
    return 1


def worker(src, store_path, sparse_path, wid, W, cell_deg, quant, eps, out_path):
    try:
        os.nice(15)                       # yield to the user's interactive apps
    except OSError:
        pass
    with open(sparse_path, "rb") as f:
        sparse = pickle.load(f)
    ncount = os.path.getsize(store_path) // 16
    sf = open(store_path, "rb")
    mm = mmap.mmap(sf.fileno(), 0, access=mmap.ACCESS_READ)
    id_at = struct.Struct("<q").unpack_from
    rec_at = struct.Struct("<qii").unpack_from
    br = bisect.bisect_right

    def lookup(r):
        j = br(sparse, r) - 1
        if j < 0:
            return None
        a = j * STRIDE
        b = a + STRIDE
        if b > ncount:
            b = ncount
        while a < b:
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

    cellbuf = {}
    cellcnt = {}
    bb = [1e9, 1e9, -1e9, -1e9]
    nfeat = 0
    i = 0
    t0 = time.time()
    for _, _w, tags, refs in nbpbf.elements(src, want_nodes=False):
        if i % W == wid:
            nfeat += _emit_way(tags, refs, lookup, eps, quant, cell_deg,
                               cellbuf, cellcnt, bb)
        i += 1
    mm.close()
    sf.close()
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump((cellbuf, cellcnt, bb, nfeat), f, protocol=4)
    os.replace(tmp, out_path)             # atomic: shard exists only if complete
    print("  worker %d: %d feats, %d cells, %.0fs"
          % (wid, nfeat, len(cellbuf), time.time() - t0), flush=True)


def _finalize(dst, name, cell_deg, quant, cellbuf, cellcnt, places, bb):
    minla, minlo, maxla, maxlo = bb
    for la, lo, nm, rank in places:       # place labels as single-point feats
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
    directory = []
    payload = bytearray()
    for key, buf in cellbuf.items():
        body = bytearray()
        _wv(body, cellcnt[key])
        body += buf
        comp = lzma.compress(bytes(body), preset=9)
        directory.append((key[0], key[1], len(payload), len(comp)))
        payload += comp
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
        f.write(struct.pack("<QII", len(payload), len(places_comp), len(places)))
        for cy, cx, off, zl in directory:
            f.write(struct.pack("<iiQI", cy, cx, off, zl))
        f.write(payload)
        f.write(places_comp)
    return len(directory), (minla, minlo, maxla, maxlo)


def main(src, store_path, dst, name, cell_deg, W, quant=100000, eps_px=1.2):
    import subprocess
    import resource
    t0 = time.time()
    eps = eps_px / quant
    ncount = os.path.getsize(store_path) // 16
    print("rebuild sparse from store (%d nodes)..." % ncount, flush=True)
    sparse_path = dst + ".sparse.pkl"
    if not os.path.exists(sparse_path):
        sparse = _sparse_from_store(store_path, STRIDE)
        with open(sparse_path + ".tmp", "wb") as f:
            pickle.dump(sparse, f, protocol=4)
        os.replace(sparse_path + ".tmp", sparse_path)
    print("  sparse ready  %.0fs" % (time.time() - t0), flush=True)
    print("scan/cache places...", flush=True)
    places = _scan_places(src, dst + ".places.pkl")
    print("  places %d  %.0fs" % (len(places), time.time() - t0), flush=True)

    procs = []
    shards = [dst + ".shard%d.pkl" % wid for wid in range(W)]
    for wid in range(W):
        if os.path.exists(shards[wid]):
            print("  shard %d already done, skip" % wid, flush=True)
            continue
        p = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--worker", src,
             store_path, sparse_path, str(wid), str(W), repr(cell_deg),
             str(quant), repr(eps), shards[wid]])
        procs.append(p)
    for p in procs:
        if p.wait() != 0:
            raise RuntimeError("a worker failed; re-run to resume")
    print("  workers done  %.0fs; merging..." % (time.time() - t0), flush=True)

    cellbuf = {}
    cellcnt = {}
    bb = [1e9, 1e9, -1e9, -1e9]
    nfeat = 0
    for sp in shards:
        with open(sp, "rb") as f:
            cb, cc, sbb, snf = pickle.load(f)
        for key, buf in cb.items():
            if key in cellbuf:
                cellbuf[key] += buf
                cellcnt[key] += cc[key]
            else:
                cellbuf[key] = buf
                cellcnt[key] = cc[key]
        bb[0] = min(bb[0], sbb[0])
        bb[1] = min(bb[1], sbb[1])
        bb[2] = max(bb[2], sbb[2])
        bb[3] = max(bb[3], sbb[3])
        nfeat += snf
    nc, bbox = _finalize(dst, name, cell_deg, quant, cellbuf, cellcnt, places, bb)
    for sp in shards:
        os.remove(sp)
    os.remove(sparse_path)
    if not os.environ.get("NB_KEEP_STORE"):
        try:
            os.remove(store_path)
        except OSError:
            pass
    sz = os.path.getsize(dst)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0
    print("%s: %d features, %d cells, %.2f MB  %.0fs  peakRSS %.2f GB"
          % (dst, nfeat, nc, sz / 1048576, time.time() - t0, rss), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        (_, _, src, store_path, sparse_path, wid, W, cell_deg,
         quant, eps, out) = sys.argv
        worker(src, store_path, sparse_path, int(wid), int(W), float(cell_deg),
               int(quant), float(eps), out)
    else:
        src, store_path, dst, name, cell_deg = sys.argv[1:6]
        W = int(sys.argv[6]) if len(sys.argv) > 6 else 4
        main(src, store_path, dst, name, float(cell_deg), W)
