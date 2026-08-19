#!/usr/bin/env python3
"""Headless contract for Sequencer's Arrange / Edit / Mix view transitions.

Two things are held still here, and they pull in opposite directions:

* The PRIMARY view stack is *navigation* — three places you go — so it moves
  under nbtransitions policy, with a direction that agrees with the view bar.
* The NESTED editor stack (wave / kit / empty) is *content substitution*: the
  same place showing whatever the selection is about. It stays instant, because
  sliding it would claim the user had navigated somewhere when all they did was
  click a different clip.

A passing run means the primary stack goes through the pager and the nested one
deliberately does not.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "sequencer.py")
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


def calls(attr):
    """Every `<something>.attr(...)` call in the module."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def receiver(node):
    """`self.stack.foo()` -> "stack"; anything else -> None."""
    value = node.func.value
    return value.attr if isinstance(value, ast.Attribute) else None


def names_attr(node, attr):
    """True when the call's positional args include `self.<attr>`."""
    return any(isinstance(a, ast.Attribute) and a.attr == attr
               for a in node.args)


# ---- one pager, over the primary stack, in view-bar order -----------------
made = calls("PageSwitcher")
check(len(made) == 1, "Sequencer builds exactly one PageSwitcher")

order = None
duration_token = None
if made:
    check(names_attr(made[0], "stack"),
          "the pager is built over the primary view stack")
    for kw in made[0].keywords:
        if kw.arg == "order":
            order = ast.literal_eval(kw.value)
        elif kw.arg == "duration" and isinstance(kw.value, ast.Attribute):
            duration_token = kw.value.attr
check(order == ["arrange", "edit", "mix"], "view order matches the view bar")
check(duration_token == "PAGE", "the view switch runs at the PAGE duration")

# ---- the primary stack is never switched behind the pager's back ----------
bypasses = [n.lineno for n in calls("set_visible_child_name")
            if receiver(n) == "stack"]
check(not bypasses,
      "primary view navigation never bypasses policy (lines %s)"
      % (bypasses or "none",))

switches = [n for n in calls("switch") if receiver(n) == "_view_pager"]
check(len(switches) >= 2,
      "the pager carries both the opening view and _set_view's switches")

# The construction-time switch must name NONE explicitly: without it the very
# first switch would try to animate a page in before the window is on screen,
# and Edit would then have no page to have come FROM.
initial = [n for n in switches
           if any(kw.arg == "direction" and isinstance(kw.value, ast.Attribute)
                  and kw.value.attr == "NONE" for kw in n.keywords)]
check(len(initial) == 1,
      "the opening view is established once, with an explicit NONE")

# ---- the nested editor stack stays an instant substitution ----------------
nested_direct = [n for n in calls("set_visible_child_name")
                 if receiver(n) == "edit_stack"]
check(len(nested_direct) == 1,
      "the wave/kit/empty editor stack still switches directly")
check(not any(names_attr(n, "edit_stack") for n in made),
      "no pager was put over the nested editor stack")

nested_none = [n for n in calls("set_transition_type")
               if receiver(n) == "edit_stack" and n.args
               and isinstance(n.args[0], ast.Attribute)
               and n.args[0].attr == "NONE"]
check(len(nested_none) == 1,
      "the nested editor stack is still explicitly NONE")

# ---- direction resolution, against the real PageSwitcher ------------------


class Stack:
    """Just enough stack for the pager: it sets a type, a duration and a name."""

    def __init__(self):
        self.name = None

    def set_transition_duration(self, _ms):
        pass

    def set_transition_type(self, _kind):
        pass

    def set_visible_child_name(self, name):
        self.name = name

    def get_visible_child_name(self):
        return self.name


stack = Stack()
pager = nbtransitions.PageSwitcher(stack, order=["arrange", "edit", "mix"],
                                   duration=nbtransitions.PAGE)
pager.switch("arrange", direction=nbtransitions.NONE)
check(pager.last_kind == nbtransitions.NONE,
      "establishing the opening view animates nothing")
check(stack.name == "arrange", "the stack lands on Arrange regardless")

check(pager.direction_to("edit") == nbtransitions.FORWARD,
      "Arrange to Edit resolves forward")
pager.switch("edit")
check(pager.direction_to("mix") == nbtransitions.FORWARD,
      "Edit to Mix resolves forward")
pager.switch("mix")
check(pager.direction_to("arrange") == nbtransitions.BACK,
      "Mix to Arrange resolves back")
pager.switch("arrange")
check(stack.name == "arrange", "every switch lands on the page asked for")

# Edit opened with no clip to be about bounces back to Arrange, which is
# already showing: that must not invent a slide from a page nobody left.
check(pager.direction_to("arrange") == nbtransitions.CROSSFADE,
      "re-showing the current view does not slide")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
