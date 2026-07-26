#!/usr/bin/env python3
"""Headless self-test of the ported reader in de/maps.py: stub GTK/nbapp, import
the real app module, and exercise NBM2 + search against a known-good pack."""
import sys
import types

_gi = types.ModuleType("gi")
_gi.require_version = lambda *a, **k: None
_repo = types.ModuleType("gi.repository")


class _Stub:
    def __getattr__(self, k):
        return _Stub()

    def __call__(self, *a, **k):
        return _Stub()


for _n in ("Gtk", "Gdk", "GLib"):
    setattr(_repo, _n, _Stub())
sys.modules["gi"] = _gi
sys.modules["gi.repository"] = _repo

_nbapp = types.ModuleType("nbapp")


class AppWindow:
    app_name = ""
    menus = ()

    def __init__(self, *a, **k):
        self.content = _Stub()

    def menu_items(self, name):
        return []

    def close(self):
        pass


_nbapp.AppWindow = AppWindow
_nbapp.run = lambda *a, **k: None
sys.modules["nbapp"] = _nbapp
sys.modules["nbicons"] = types.ModuleType("nbicons")

sys.path.insert(0, "/home/ben/Documents/notebookos-linux/buildroot/board/"
                   "notebookos/rootfs-overlay/opt/notebook/de")
import maps  # noqa: E402

pack = sys.argv[1] if len(sys.argv) > 1 else "oregon3.good.nbm2"
p = maps.NBM2(pack)
print("name  :", p.name, " cells:", len(p.dir), " places:", p.places_cnt,
      " cell_deg:", p.cell_deg)
places = p.places()
assert len(places) == p.places_cnt, "places count mismatch"
rc = {}
for rank, nm, la, lo in places:
    rc[rank] = rc.get(rank, 0) + 1
print("ranks :", dict(sorted(rc.items())))
for q in ("portland", "salem", "eugene"):
    hits = [(nm, round(la, 3), round(lo, 3)) for rank, nm, la, lo in places
            if q in nm.lower()]
    hits.sort()
    print("search %-9s -> %d, top %s" % (q, len(hits), hits[:2]))
k = next(iter(p.dir))
feats = p.cell(*k)
cats = {}
for f in feats:
    cats[f[0]] = cats.get(f[0], 0) + 1
print("cell", k, "->", len(feats), "feats, cats", dict(sorted(cats.items())))
# spot-check a feature has mercator points + bbox of the right shape
f0 = next(f for f in feats if f[0] in (1, 2))
print("sample feat: cat", f0[0], "npts", len(f0[3]),
      "bbox", tuple(round(v, 2) for v in f0[4]))
print("merc(45.52,-122.67) =", tuple(round(v, 3) for v in maps._merc(45.52, -122.67)))
print("OK: reader port verified")
