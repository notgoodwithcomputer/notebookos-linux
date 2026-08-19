#!/usr/bin/env python3
"""Headless contract for Packages' Installed / Updates / Sources navigation.

The sidebar is the app's only navigation: three views, in a fixed top-to-bottom
order, and clicking down the list has to read as going forward. That is the
whole reason this stack goes through nbtransitions rather than
set_visible_child_name — a direction the sidebar order decides once, instead of
every call site guessing.

Two things this pins that a rendering pass cannot see:

* **The switch never bypasses policy.** A direct set_visible_child_name on the
  primary stack still lands on the right page, so nothing looks broken; it just
  quietly slides the wrong way (or animates where policy said be still). Only a
  source check catches that.
* **Sources is re-scanned BEFORE it slides in.** _on_nav rebuilds the source
  rows from /proc/mounts on the way in. If the switch ran first, the page
  visibly animating in would be the launch-time snapshot, and the newly-plugged
  stick would appear a frame later.

A passing run means the primary stack is the pager's, the opening view is
established with an explicit NONE, and the order matches the sidebar.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
SOURCE = os.path.join(DE, "packages.py")
sys.path.insert(0, DE)
import nbtransitions  # noqa: E402

VIEWS = ["installed", "updates", "sources"]

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    SRC = fh.read()
tree = ast.parse(SRC, SOURCE)


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


def function(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---- the module reaches the primitive at all ------------------------------
imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
check("nbtransitions" in imports, "Packages imports nbtransitions")

# ---- one pager, over the primary stack, in sidebar order ------------------
made = calls("PageSwitcher")
check(len(made) == 1, "Packages builds exactly one PageSwitcher")

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
check(order == VIEWS, "view order matches the sidebar, top to bottom")
check(duration_token == "PAGE", "the view switch runs at the PAGE duration")

# ---- the primary stack is never switched behind the pager's back ----------
bypasses = [n.lineno for n in calls("set_visible_child_name")
            if receiver(n) == "stack"]
check(not bypasses,
      "primary view navigation never bypasses policy (lines %s)"
      % (bypasses or "none",))

# The stack is still built instant: the three pages are added during __init__,
# and a transition type set before that would animate the construction itself.
stack_none = [n for n in calls("set_transition_type")
              if receiver(n) == "stack" and n.args
              and isinstance(n.args[0], ast.Attribute)
              and n.args[0].attr == "NONE"]
check(len(stack_none) == 1, "the stack is still explicitly NONE as it is built")

# ---- the opening view is established, once, with an explicit NONE ---------
switches = [n for n in calls("switch") if receiver(n) == "_pager"]
check(len(switches) >= 2,
      "the pager carries both the opening view and _on_nav's switches")

initial = [n for n in switches
           if any(kw.arg == "direction" and isinstance(kw.value, ast.Attribute)
                  and kw.value.attr == "NONE" for kw in n.keywords)]
check(len(initial) == 1,
      "the opening view is established once, with an explicit NONE")
if initial:
    # View persistence (2026-08) made the opening switch carry self.view —
    # restored from the store — rather than a constant. The first-run
    # contract survives as the attribute's own initialiser: self.view is
    # born "installed" before any prefs load can override it.
    opening = initial[0].args[0] if initial[0].args else None
    opens_attr = (isinstance(opening, ast.Attribute)
                  and opening.attr == "view")
    check(opens_attr and 'self.view = "installed"' in SRC,
          "the app opens on the restored view, born Installed")

# ---- _on_nav keeps its order: style, refresh, then switch -----------------
nav = function("_on_nav")
check(nav is not None, "_on_nav is still the single navigation entry point")
if nav is not None:
    nav_switch = [n for n in ast.walk(nav) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "switch" and receiver(n) == "_pager"]
    check(len(nav_switch) == 1, "_on_nav switches through the pager exactly once")

    refresh = [n for n in ast.walk(nav) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_refresh_sources"]
    check(len(refresh) == 1, "_on_nav still re-scans Sources on the way in")
    if refresh and nav_switch:
        check(refresh[0].lineno < nav_switch[0].lineno,
              "Sources is re-scanned BEFORE the page slides in")

    # The active class moves in _set_nav_active now (the rail is toggle rows,
    # and lighting one from inside _on_nav has to happen with the rows' own
    # handlers blocked — see that method); _on_nav calls it once.
    lit = [n for n in ast.walk(nav) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute)
           and n.func.attr == "_set_nav_active"]
    setter = function("_set_nav_active")
    styling = [n for n in ast.walk(setter) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr in ("add_class", "remove_class")] if setter else []
    check(len(lit) == 1 and len(styling) == 2,
          "_on_nav still moves the sidebar's active class")
    if styling and nav_switch:
        check(max(n.lineno for n in styling) < nav_switch[0].lineno,
              "the sidebar row is restyled before the switch, not after")

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
pager = nbtransitions.PageSwitcher(stack, order=VIEWS,
                                   duration=nbtransitions.PAGE)
pager.switch("installed", direction=nbtransitions.NONE)
check(pager.last_kind == nbtransitions.NONE,
      "establishing the opening view animates nothing")
check(stack.name == "installed", "the stack lands on Installed regardless")

check(pager.direction_to("updates") == nbtransitions.FORWARD,
      "Installed to Updates resolves forward")
pager.switch("updates")
check(pager.direction_to("sources") == nbtransitions.FORWARD,
      "Updates to Sources resolves forward")
pager.switch("sources")
check(pager.direction_to("installed") == nbtransitions.BACK,
      "Sources back to Installed resolves back")
pager.switch("installed")
check(stack.name == "installed", "every switch lands on the page asked for")

# The View menu can pick the view that is already showing, and Find… jumps to
# Installed from Installed. Neither is navigation, so neither may slide.
check(pager.direction_to("installed") == nbtransitions.CROSSFADE,
      "re-selecting the current view does not slide")

# Whatever policy decides, the page is the page: a still session is the same
# end state, not a degraded one.
for view in VIEWS:
    pager.switch(view)
    if stack.name != view:
        break
check(stack.name == VIEWS[-1], "the whole sidebar is reachable in order")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
