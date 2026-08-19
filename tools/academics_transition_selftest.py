#!/usr/bin/env python3
"""Headless contract for Academics workspace navigation transitions."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "academics.py")
sys.path.insert(0, DE)
import nbtransitions  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    tree = ast.parse(fh.read(), SOURCE)
made = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "PageSwitcher"]
check(len(made) == 1, "Academics builds one primary PageSwitcher")
order = None
if made:
    for kw in made[0].keywords:
        if kw.arg == "order":
            order = ast.literal_eval(kw.value)
check(order == ["notes", "schedule", "homework"],
      "workspace order matches the sidebar")
bypasses = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "set_visible_child_name"
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "stack"]
check(not bypasses, "primary workspace navigation never bypasses policy")


class Stack:
    def set_transition_duration(self, _ms): pass
    def set_transition_type(self, _kind): pass
    def set_visible_child_name(self, _name): pass


pager = nbtransitions.PageSwitcher(
    Stack(), order=["notes", "schedule", "homework"])
pager.switch("notes", direction=nbtransitions.NONE)
check(pager.direction_to("homework") == nbtransitions.FORWARD,
      "moving rightward through workspaces resolves forward")
pager.switch("homework")
check(pager.direction_to("notes") == nbtransitions.BACK,
      "returning to Notes resolves back")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
