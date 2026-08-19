#!/usr/bin/env python3
"""Headless contract for Video's Storyboard / Timeline view transition.

The segmented control at the top of the editor is *navigation*: two places you
go, in a stated left-to-right order. So the stack under it moves through the
shared page-switch primitive, with a direction that agrees with the control —
Storyboard -> Timeline slides forward, Timeline -> Storyboard slides back —
instead of every switch looking identical.

Two things are held still here:

* the switch itself goes through the pager, never behind its back, so policy
  (Reduced Motion, NB_ACCEL, no frame clock) gets to decide every time; and
* the active-button styling still lands AFTER the switch, because the lit
  button and the shown page are one state and the button must not lead.

A passing run means the migration kept both.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "video.py")
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
    """`self.tl_stack.foo()` -> "tl_stack"; anything else -> None."""
    value = node.func.value
    return value.attr if isinstance(value, ast.Attribute) else None


def names_attr(node, attr):
    """True when the call's positional args include `self.<attr>`."""
    return any(isinstance(a, ast.Attribute) and a.attr == attr
               for a in node.args)


def method(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---- the module reaches the primitive at all -------------------------------
check(any(isinstance(n, ast.Import)
          and any(a.name == "nbtransitions" for a in n.names)
          for n in ast.walk(tree)),
      "video.py imports nbtransitions")

# ---- one pager, over the timeline stack, in segmented-control order --------
made = calls("PageSwitcher")
check(len(made) == 1, "Video builds exactly one PageSwitcher")

order = None
duration_token = None
if made:
    check(names_attr(made[0], "tl_stack"),
          "the pager is built over the Storyboard/Timeline stack")
    for kw in made[0].keywords:
        if kw.arg == "order":
            order = ast.literal_eval(kw.value)
        elif kw.arg == "duration" and isinstance(kw.value, ast.Attribute):
            duration_token = kw.value.attr
check(order == ["story", "time"],
      "view order matches the segmented control (Storyboard, Timeline)")
check(duration_token == "PAGE", "the view switch runs at the PAGE duration")

# The stack is still explicitly NONE at construction: the pager sets a type on
# every switch, but between add_named and the first switch the stack must not
# be left on whatever a future GTK default happens to be.
constructed_none = [n for n in calls("set_transition_type")
                    if receiver(n) == "tl_stack" and n.args
                    and isinstance(n.args[0], ast.Attribute)
                    and n.args[0].attr == "NONE"]
check(len(constructed_none) == 1,
      "the timeline stack is explicitly NONE while being constructed")

# ---- the stack is never switched behind the pager's back -------------------
bypasses = [n.lineno for n in calls("set_visible_child_name")
            if receiver(n) == "tl_stack"]
check(not bypasses,
      "view navigation never bypasses policy (lines %s)"
      % (bypasses or "none",))

switches = [n for n in calls("switch") if receiver(n) == "_timeline_pager"]
check(len(switches) >= 2,
      "the pager carries both the opening view and _set_view's switch")

# The construction-time switch must name NONE explicitly: without it the very
# first switch would try to animate a page in before the window is on screen,
# and the Timeline would then have no page to have come FROM.
initial = [n for n in switches
           if any(kw.arg == "direction" and isinstance(kw.value, ast.Attribute)
                  and kw.value.attr == "NONE" for kw in n.keywords)]
check(len(initial) == 1,
      "the opening view is established once, with an explicit NONE")

# ---- _set_view still lights the right button, still after the switch -------
setview = method("_set_view")
check(setview is not None, "_set_view is still where the view change lives")
if setview is not None:
    inner = [n for n in ast.walk(setview) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)]
    switch_lines = [n.lineno for n in inner
                    if n.func.attr == "switch"
                    and receiver(n) == "_timeline_pager"]
    check(len(switch_lines) == 1,
          "_set_view switches through the pager exactly once")
    # The styling is a pair of add_class/remove_class calls on the two segmented
    # buttons' style contexts; all that is asserted is that they are still there
    # and still run after the page has been asked for.
    class_lines = [n.lineno for n in inner
                   if n.func.attr in ("add_class", "remove_class")]
    check(len(class_lines) == 4,
          "both buttons are still styled on every view change")
    check(bool(switch_lines) and bool(class_lines)
          and min(class_lines) > max(switch_lines),
          "active-button styling still happens after the switch")
    check(not [n for n in inner if n.func.attr == "set_visible_child_name"],
          "_set_view no longer touches the stack directly")

# ---- direction resolution, against the real PageSwitcher -------------------


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
pager = nbtransitions.PageSwitcher(stack, order=["story", "time"],
                                  duration=nbtransitions.PAGE)
pager.switch("story", direction=nbtransitions.NONE)
check(pager.last_kind == nbtransitions.NONE,
      "establishing the opening view animates nothing")
check(stack.name == "story", "the stack lands on the storyboard regardless")

check(pager.direction_to("time") == nbtransitions.FORWARD,
      "Storyboard to Timeline resolves forward")
pager.switch("time")
check(stack.name == "time", "the forward switch lands on the Timeline")
check(pager.direction_to("story") == nbtransitions.BACK,
      "Timeline to Storyboard resolves back")
pager.switch("story")
check(stack.name == "story", "the back switch lands on the Storyboard")

# Both menu entries and both buttons can name the view already showing; that
# must not invent a slide from a page nobody left.
check(pager.direction_to("story") == nbtransitions.CROSSFADE,
      "re-showing the current view does not slide")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
