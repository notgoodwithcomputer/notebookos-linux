#!/usr/bin/env python3
"""Headless contract for Cookbook's primary state navigation."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "cookbook.py")
sys.path.insert(0, DE)
import cookbook  # noqa: E402
import nbtransitions  # noqa: E402

fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    tree = ast.parse(fh.read(), SOURCE)
made = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "PageSwitcher"]
check(len(made) == 1, "Cookbook builds one primary PageSwitcher")
order = next((ast.literal_eval(k.value) for k in made[0].keywords
              if k.arg == "order"), None) if made else None
check(order == ["empty", "editor", "cook"],
      "primary order follows empty, recipe, cooking hierarchy")
bypasses = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "set_visible_child_name"
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "stack"]
check(not bypasses, "primary state changes never bypass policy")
nested = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
          and isinstance(n.func, ast.Attribute)
          and n.func.attr == "set_visible_child_name"]
check(len(nested) >= 4, "ingredient/method read-edit substitutions stay direct")


class Stack:
    def __init__(self, name="empty"):
        self.name, self.names = name, []
    def get_visible_child_name(self): return self.name
    def set_transition_duration(self, _ms): pass
    def set_transition_type(self, _kind): pass
    def set_visible_child_name(self, name): self.name = name; self.names.append(name)


app = cookbook.Cookbook.__new__(cookbook.Cookbook)
app.stack = Stack()
app._main_pager = nbtransitions.PageSwitcher(
    app.stack, order=["empty", "editor", "cook"])
app._switch_main("empty")
check(app._main_pager.target == "empty" and app._main_pager.last_kind == nbtransitions.NONE,
      "the actual opening state is recorded without animation")
before = list(app.stack.names)
app._switch_main("empty")
check(app.stack.names == before, "refreshing the current state schedules no switch")
check(app._main_pager.direction_to("editor") == nbtransitions.FORWARD,
      "empty to recipe resolves forward")
app._switch_main("editor")
check(app._main_pager.direction_to("cook") == nbtransitions.FORWARD,
      "recipe to cooking resolves forward")
app._switch_main("cook")
check(app._main_pager.direction_to("editor") == nbtransitions.BACK,
      "leaving cooking resolves back")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
