#!/usr/bin/env python3
"""Parallel font audits keep their hand-drawn glyph overrides isolated."""
import concurrent.futures
import importlib.util
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "gen_gba_font.py")
spec = importlib.util.spec_from_file_location("gen_gba_font", PATH)
fontgen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fontgen)

barrier = threading.Barrier(2)
real_rows = fontgen.glyph_rows


def synchronized(font, ch, overrides=None):
    if ch == " ":
        barrier.wait(timeout=5)
    return real_rows(font, ch, overrides)


fontgen.glyph_rows = synchronized
left_art = ("#",)
right_art = (".......#",)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(fontgen.rasterise, fontgen.FONT, {"~": left_art})
        right = pool.submit(fontgen.rasterise, fontgen.FONT, {"~": right_art})
        left_rows = left.result()[1][ord("~")]
        right_rows = right.result()[1][ord("~")]
finally:
    fontgen.glyph_rows = real_rows

assert left_rows == fontgen._pack(left_art)
assert right_rows == fontgen._pack(right_art)
assert left_rows != right_rows
assert fontgen.OVERRIDES.get("~") is None
assert "global OVERRIDES" not in open(PATH, encoding="utf-8").read()

print("GBA FONT OVERRIDE CONCURRENCY SELFTEST: 5 checks, all pass")
print("RESULT: ALL PASS")
