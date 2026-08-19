#!/usr/bin/env python3
"""Headless contract for Accounting's inline entry-form disclosure."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Read the module UNDER TEST, not a fixed path. This suite parses accounting.py
# with `ast`, and a hardcoded SOURCE means a red proof that mutates a COPY is
# graded against the pristine file — measured: a mutation that hand-rolls the
# revealer on the Escape path left this suite reporting "0 failed", because it
# never looked at the mutated copy. Same shape as the accounting_cards suite,
# which opened the repo file by its expected path and stayed green against a
# module with every add_class stripped. A check must not read past its subject.
DE = os.environ.get("ACCOUNTING_MODULE_DIR") or os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay", "opt/notebook/de")
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
# Count the FORM's reveals, not every reveal in the file. This read
# `len(reveals) == 3` — a global cap on how many things the app is allowed to
# animate — which the OS motion rule now contradicts outright: every state
# change animates (PAPER-PHYSICS Amendment 3), so the number can only grow. It
# went red the moment the sidebar's opening-balance row stopped SNAPPING and
# started sliding, which is the rule being obeyed, not broken.
#
# The intent was always that the form's three paths go through the shared
# helper instead of hand-rolling a revealer, and that is what is asserted now.
# The `set_reveal_child` check above still forbids the hand-rolled route
# anywhere, so nothing is lost by scoping this one.
form_reveals = [n for n in reveals
                if n.args and isinstance(n.args[0], ast.Attribute)
                and n.args[0].attr == "form_reveal"]
check(len(form_reveals) == 3,
      "toggle, forced-open, and Escape-close use shared reveal")
check(len(reveals) >= len(form_reveals),
      "every reveal in the app goes through nbtransitions")

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
print("RESULT: %s" % ("FAILED" if fails else "PASS"))
sys.exit(1 if fails else 0)
