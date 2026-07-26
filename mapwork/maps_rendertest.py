#!/usr/bin/env python3
"""Exercise the REAL de/maps.py render path off-guest: build a Maps instance
without __init__, set the view fields, call _render_surface, save the surface.
Confirms the shipping render code (not just a stand-in) works end to end."""
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
    def __init__(self, *a, **k):
        self.content = _Stub()


_nbapp.AppWindow = AppWindow
_nbapp.run = lambda *a, **k: None
sys.modules["nbapp"] = _nbapp
sys.modules["nbicons"] = types.ModuleType("nbicons")
sys.path.insert(0, "/home/ben/Documents/notebookos-linux/buildroot/board/"
                   "notebookos/rootfs-overlay/opt/notebook/de")
import maps  # noqa: E402


def shot(pack_path, lat, lon, scale, out, w=900, h=620):
    mp = maps.Maps.__new__(maps.Maps)     # skip GTK __init__
    mp.pack = maps.NBM2(pack_path)
    mp.scale = scale
    mp.cx, mp.cy = maps._merc(lat, lon)
    mp._surface = None
    mp._surf_size = mp._surf_scale = None
    mp._surf_cx = mp._surf_cy = 0.0
    maps.Maps._render_surface(mp, w, h)
    mp._surface.write_to_png(out)
    # count what the shipping code decided to draw
    cy0, cy1, cx0, cx1, _ = maps.Maps._visible_cells(mp, w, h)
    print("  %-22s scale=%-7g out=%s" % ("(%.3f,%.3f)" % (lat, lon), scale, out))


if __name__ == "__main__":
    shot("monaco.nbm2", 43.7384, 7.4246, 90000, "app_monaco.png")
    shot("oregon3.good.nbm2", 44.9429, -123.0351, 20000, "app_salem.png")
    shot("oregon3.good.nbm2", 44.9429, -123.0351, 5000, "app_salem_region.png")
    print("OK: shipping _render_surface path works")
