#!/usr/bin/env python3
"""Headless checks for bounded decompression of external map packs."""
import io
import lzma
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import maps  # noqa: E402

failed = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failed.append(name)


small = lzma.compress(b"map data")
check("an in-budget member decompresses normally",
      maps._lzma_limited(small, 64) == b"map data")

bomb = lzma.compress(b"x" * 4096)
try:
    maps._lzma_limited(bomb, 1024)
    refused = False
except ValueError:
    refused = True
check("an expanding member is refused at its output budget", refused)

# Exercise the real cell path too: a corrupt directory length must be rejected
# before read(zl) can consume the rest of a multi-gigabyte pack.
pack = maps.NBM2.__new__(maps.NBM2)
pack.dir = {(0, 0): (0, maps.PACK_COMPRESSED_MAX + 1)}
pack.payload_base = 0
pack.f = io.BytesIO(bomb)
pack._cache = {}
pack._order = []
check("an oversized compressed cell degrades to an empty cell",
      pack.cell(0, 0) == () and pack.f.tell() == 0)


class FakePack:
    def __init__(self, path):
        if path == "damaged":
            raise ValueError("damaged")
        self.path = path
        self.closed = 0

    def close(self):
        self.closed += 1


class Canvas:
    def queue_draw(self):
        pass


app = maps.Maps.__new__(maps.Maps)
old = FakePack("old")
app.pack = old
app._view_gen = 0
app._view_anim = None
app._view_moving = False
app._empty = None
app._hi = None
app.canvas = Canvas()
app._position_for = lambda _path: None
app._invalidate = lambda: None
real_pack = maps.NBM2
maps.NBM2 = FakePack
try:
    check("opening a new region closes the superseded pack",
          app._open_map("new") and old.closed == 1
          and app.pack.path == "new")
    current = app.pack
    check("a damaged region leaves the working map open",
          not app._open_map("damaged") and app.pack is current
          and current.closed == 0)
    app._on_destroy()
    check("closing Maps releases the final pack exactly once",
          current.closed == 1 and app.pack is None)
finally:
    maps.NBM2 = real_pack

print("RESULT: " + ("ALL PASS" if not failed else "%d FAILED" % len(failed)))
raise SystemExit(bool(failed))
