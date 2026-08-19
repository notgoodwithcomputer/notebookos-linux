#!/usr/bin/env python3
"""Headless regression for a thumbnail idle dispatched during close."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import media  # noqa: E402

viewer = media.MediaViewer.__new__(media.MediaViewer)
viewer._closed = True
viewer._thumb_idle_id = 77
viewer._thumb_queue = ["/media/large-picture.tiff"]
viewer._thumb_slow = ["/media/slow-picture.webp"]
viewer._strip_imgs = {"/media/large-picture.tiff": object()}
viewer._thumb_cache = {}

decoded = []
real_decode = media._thumbnail_fast
media._thumbnail_fast = lambda path: decoded.append(path)
try:
    keep = viewer._thumb_tick()
finally:
    media._thumbnail_fast = real_decode

ok = (keep is False and decoded == [] and viewer._thumb_idle_id == 0
      and viewer._thumb_queue == [] and viewer._thumb_slow == [])
print(("PASS" if ok else "FAIL")
      + " a dispatched thumbnail idle is inert after close")
print("RESULT: %s" % ("PASS" if ok else "FAILED"))
raise SystemExit(not ok)
