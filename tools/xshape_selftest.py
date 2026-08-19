#!/usr/bin/env python3
"""Display-free fixed-width validation checks for xshape rectangles."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import xshape  # noqa: E402

assert xshape.validated_rects([]) == []
assert xshape.validated_rects([(0, 0, 1920, 46)]) == [(0, 0, 1920, 46)]
assert xshape.validated_rects([(-32768, 32767, 65535, 1)]) == [
    (-32768, 32767, 65535, 1)]
for bad in (None, [(0, 0, -1, 20)], [(0, 0, 1 << 20, 20)],
            [(32768, 0, 20, 20)], [(0, -32769, 20, 20)],
            [(0, 0, 20)], [("bad", 0, 20, 20)],
            [(float("inf"), 0, 20, 20)]):
    assert xshape.validated_rects(bad) is None, bad

print("PASS xshape rejects rectangles that would wrap in XRectangle")
print("RESULT: PASS")
