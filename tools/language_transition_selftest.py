#!/usr/bin/env python3
"""Static/headless contract for Language's hierarchical page movement."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "language.py")
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
check(len(made) == 1, "Language builds one primary PageSwitcher")
order = None
if made:
    for kw in made[0].keywords:
        if kw.arg == "order":
            try:
                order = ast.literal_eval(kw.value)
            except Exception:
                pass
check(order == ["home", "course", "lesson", "page"],
      "page order follows the course hierarchy")

bypasses = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "set_visible_child_name"
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "stack"]
check(not bypasses, "primary navigation never bypasses the shared policy")


class Stack:
    def __init__(self): self.names = []
    def set_transition_duration(self, _ms): pass
    def set_transition_type(self, _kind): pass
    def set_visible_child_name(self, name): self.names.append(name)


stack = Stack()
pager = nbtransitions.PageSwitcher(
    stack, order=["home", "course", "lesson", "page"])
pager.switch("home", direction=nbtransitions.NONE)
check(pager.direction_to("course") == nbtransitions.FORWARD,
      "moving into a course resolves to the forward direction")
pager.switch("course")
pager.switch("lesson")
check(pager.direction_to("course") == nbtransitions.BACK,
      "returning from a lesson resolves to the back direction")
pager.switch("course")
check(stack.names == ["home", "course", "lesson", "course"],
      "the policy still delivers every requested page")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
