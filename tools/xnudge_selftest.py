#!/usr/bin/env python3
"""Headless argument-safety checks for the X repaint nudge helper."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot", "board", "notebookos",
                    "rootfs-overlay", "opt", "notebook", "de", "xnudge.py")
spec = importlib.util.spec_from_file_location("xnudge", PATH)
xnudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xnudge)

assert xnudge.validated_args(["xnudge", "0x20", "800", "30"]) == (32, 800, 30)
for args in (["xnudge"], ["xnudge", "0", "800", "30"],
             ["xnudge", hex(xnudge.MAX_XID + 1), "800", "30"],
             ["xnudge", str(1 << 80), "800", "30"],
             ["xnudge", "2", "0", "30"],
             ["xnudge", "2", "800", "0"],
             ["xnudge", "2", str(xnudge.MAX_X_DIMENSION + 1), "30"],
             ["xnudge", "2", "800", str(1 << 40)],
             ["xnudge", "bad", "800", "30"]):
    assert xnudge.validated_args(args) is None, args
print("PASS xnudge refuses invalid and underflowing X11 dimensions")
print("RESULT: PASS")
