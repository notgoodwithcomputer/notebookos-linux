#!/usr/bin/env python3
"""The repaint helper exits rather than polling a dead X session forever."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))
import xflushd  # noqa: E402


class Display:
    def __init__(self):
        self.flushes = 0
        self.syncs = 0

    def flush(self):
        self.flushes += 1

    def sync(self):
        self.syncs += 1
        if self.syncs == 2:
            raise RuntimeError("X connection closed")


display = Display()
pauses = []
result = xflushd.flush_loop(display, pauses.append)
assert result is False
assert (display.flushes, display.syncs) == (2, 2)
assert pauses == [0.5], pauses
print("XFLUSHD LIFECYCLE SELFTEST: 3 checks, all pass")
