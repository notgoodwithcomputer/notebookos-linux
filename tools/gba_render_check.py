#!/usr/bin/env python3
"""gba_render_check — a host-side simulator of the GBA mode-0 OBJ compositor.

No emulator is available on the build host, so this verifies that the GBA IDE's
mode-0 renderer would show the right thing: it takes a project model + a list of
placed instances, reproduces the OBJ tile/palette data the compiler emits and the
OAM the runtime builds, then composites one frame exactly as the GBA PPU does
(backdrop = BG palette[0], then each hardware object from its tiles), writing a
PNG. It follows the hardware spec independently of the C runtime, so a correct
image confirms the tile addressing / palette / positioning are right.

    tools/gba_render_check.py            -> renders a built-in demo scene to PNG
"""
import sys
import os
import struct
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "buildroot", "board", "notebookos",
                                "rootfs-overlay", "opt", "notebook", "de"))
import gbabuild  # noqa: E402

T15 = gbabuild.TRANSPARENT & 0x7FFF


def _c15_to_rgb(c):
    r = (c & 0x1F) << 3
    g = ((c >> 5) & 0x1F) << 3
    b = ((c >> 10) & 0x1F) << 3
    return (r | (r >> 5), g | (g >> 5), b | (b >> 5))


def _write_png(path, pixels, w, h, scale=1):
    """pixels: list of (r,g,b) rows*cols. Nearest-neighbour upscaled by `scale`."""
    raw = bytearray()
    for y in range(h):
        for _ in range(scale):
            raw.append(0)  # filter type 0
            for x in range(w):
                r, g, b = pixels[y * w + x]
                raw.extend((r, g, b) * scale)

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def render_scene(model, instances, out_png, screen=(240, 160), scale=3):
    """instances: list of dicts {sprite, image_index, x, y}. Composites one
    mode-0 frame (camera at 0,0) and writes it to out_png."""
    g = gbabuild._Gen(model)
    pal = g._build_obj_palette()                 # 16 x 15-bit
    # rebuild per-sprite OBJ metadata + the concatenated tile stream, exactly as
    # gen_sprites does, so tile base indices line up.
    meta, tiles = {}, []
    # _obj_tiles takes the sprite INDEX too (it looks up that sprite's colour
    # map in _spr_cmap) and returns the visual width and height separately,
    # since a sprite need not be square.
    for si, s in enumerate(model.get("sprites", [])):
        vw, vh, td = g._obj_tiles(s, si)
        base = len(tiles) // 16                  # 16 u16 per 4bpp tile
        meta[s["id"]] = {
            "base": base, "tpf": (vw // 8) * (vh // 8), "n": vw, "vw": vw,
            "vh": vh,
            "ox": gbabuild._int(s.get("ox"), vw // 2),
            "oy": gbabuild._int(s.get("oy"), vh // 2),
            "nf": max(1, len(s.get("frames") or [[]])),
        }
        tiles.extend(td)

    W, H = screen
    room_bg = gbabuild._rgb15(model.get("_bg", "#000000"), 0)
    img = [_c15_to_rgb(room_bg)] * (W * H)
    img = list(img)

    def tile_px(tileno, col, row):
        off = tileno * 16 + row * 2 + (col // 4)
        return (tiles[off] >> ((col % 4) * 4)) & 0xF

    # GBA draws OAM 0 on top; iterate reversed so earlier instances win.
    for inst in reversed(instances):
        m = meta.get(inst["sprite"])
        if not m:
            continue
        # a sprite need not be square: walk vh rows of vw pixels, with the
        # tile row stride taken from the visual WIDTH
        vw, vh = m["vw"], m["vh"]
        tw = vw // 8
        frame = inst.get("image_index", 0)
        if frame >= m["nf"]:
            frame = 0
        tile0 = m["base"] + frame * m["tpf"]
        sx = inst["x"] - m["ox"]
        sy = inst["y"] - m["oy"]
        for py in range(vh):
            dy = sy + py
            if dy < 0 or dy >= H:
                continue
            ty, r = py // 8, py % 8
            for px in range(vw):
                dx = sx + px
                if dx < 0 or dx >= W:
                    continue
                tx, c = px // 8, px % 8
                idx = tile_px(tile0 + ty * tw + tx, c, r)
                if idx == 0:
                    continue                      # transparent
                img[dy * W + dx] = _c15_to_rgb(pal[idx])

    _write_png(out_png, img, W, H, scale)
    return out_png


if __name__ == "__main__":
    def frame(fn, n):
        return [fn(i % n, i // n) for i in range(n * n)]
    # a little hero (16x16) + a 32x32 block, on a blue backdrop
    model = {
        "_bg": "#204060",
        "sprites": [
            {"id": "spr_hero", "w": 16, "h": 16, "ox": 8, "oy": 8, "frames": [
                frame(lambda x, y: 0x001F if 4 <= x < 12 and 2 <= y < 14 else T15, 16)]},
            {"id": "spr_face", "w": 16, "h": 16, "ox": 8, "oy": 8, "frames": [
                frame(lambda x, y:
                      0x0000 if (x, y) in ((5, 5), (10, 5)) else       # eyes
                      (0x0000 if y == 11 and 5 <= x <= 10 else          # mouth
                       (0x03FF if 3 <= x < 13 and 2 <= y < 14 else T15)), 16)]},
            {"id": "spr_big", "w": 32, "h": 32, "ox": 16, "oy": 16, "frames": [
                frame(lambda x, y: 0x7FFF if (x // 4 + y // 4) % 2 else 0x7C00, 32)]},
        ],
        "sounds": [], "objects": [], "rooms": [],
    }
    scene = [
        {"sprite": "spr_big", "image_index": 0, "x": 60, "y": 80},
        {"sprite": "spr_hero", "image_index": 0, "x": 140, "y": 60},
        {"sprite": "spr_face", "image_index": 0, "x": 190, "y": 100},
        {"sprite": "spr_hero", "image_index": 0, "x": 8, "y": 8},     # corner clip
    ]
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/gba_frame.png"
    render_scene(model, scene, out)
    print("wrote", out)
