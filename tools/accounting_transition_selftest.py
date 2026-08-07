#!/usr/bin/env python3
"""Headless contract for Accounting's inline entry-form disclosure."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "accounting.py")
sys.path.insert(0, DE)
import nbtransitions  # noqa: E402

fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    tree = ast.parse(fh.read(), SOURCE)
direct = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
          and isinstance(n.func, ast.Attribute)
          and n.func.attr == "set_reveal_child"]
check(len(direct) == 1 and direct[0].args
      and isinstance(direct[0].args[0], ast.Constant)
      and direct[0].args[0].value is False,
      "only construction performs a direct, hidden-state initialization")
reveals = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute)
           and n.func.attr == "reveal"
           and isinstance(n.func.value, ast.Name)
           and n.func.value.id == "nbtransitions"]
check(len(reveals) == 3, "toggle, forced-open, and Escape-close use shared reveal")

old_policy = nbtransitions.nbmotion.policy
old_reduced = nbtransitions.nbmotion.reduced_motion
nbtransitions.nbmotion.policy = lambda duration, _fade=False: duration
nbtransitions.nbmotion.reduced_motion = lambda: False
try:
    check(nbtransitions.revealer_plan(nbtransitions.SLIDE_DOWN)[0]
          == nbtransitions.SLIDE_DOWN, "opening resolves downward")
    check(nbtransitions.revealer_plan(nbtransitions.SLIDE_UP)[0]
          == nbtransitions.SLIDE_UP, "closing resolves upward")
finally:
    nbtransitions.nbmotion.policy = old_policy
    nbtransitions.nbmotion.reduced_motion = old_reduced

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
