#!/usr/bin/env python3
"""Static contract for keyboard-operable Meal Planner slots.

THE DEFECT THIS EXISTS FOR. Every one of the 21 cells in the week grid is an
action: opening one is how you say what is being eaten. They were built out of
Gtk.EventBox, which answers to the POINTER alone -- it takes no focus, is not
in the Tab ring, and reports nothing to assistive technology. There is no other
route to a slot (no list, no menu item, no shortcut), so the whole plan was
unreachable without a mouse, and a screen reader was told only that some boxes
existed.

Each cell is now a real Gtk.Button with its chrome stripped by .mp-slothit, so
it looks and measures exactly as before while Tab / Space / Enter work. The
second half of that swap is hover OWNERSHIP: the pointer is over the WRAPPER,
not over the inner .mp-slot, so the cell's hover fills have to hang off the
wrapper's state -- otherwise a cell lights up over part of itself and not the
rest.

Static and display-free: it parses the source, builds no widget and needs no X
display.

  python3 tools/mealplanner_accessibility_selftest.py
"""
import ast
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/mealplanner.py")
fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    source = fh.read()
tree = ast.parse(source, SOURCE)


def method(name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


slot_widget = method("_slot_widget")
slot_source = ast.get_source_segment(source, slot_widget) or ""
calls = [n for n in ast.walk(slot_widget) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)]
attrs = [n.func.attr for n in calls]
gtk_builds = [n.func.attr for n in calls
              if isinstance(n.func.value, ast.Name) and n.func.value.id == "Gtk"]

# -- the control itself ------------------------------------------------------

check(gtk_builds.count("Button") == 1,
      "each week slot is built as exactly one real button")
check("EventBox" not in gtk_builds,
      "week slots use no pointer-only EventBox")
# CODE, not prose. The only "EventBox" left in mealplanner.py is the comment
# recording that a real Gtk.Button is used INSTEAD of one, so the sentence
# explaining the fix was failing the check for the bug. Third time this shape
# has turned up today (music_transport_accessibility, gbaemu_selftest): a
# static guard that greps whole files reports the documentation, not the code.
_code = "\n".join(l for l in source.splitlines()
                  if not l.strip().startswith("#"))
check("EventBox" not in _code and "EventMask" not in _code
      and "add_events" not in attrs,
      "no raw Gdk event-mask plumbing survives anywhere in the app")
check(any(n.func.attr == "set_relief" for n in calls),
      "the slot button keeps neutral Papertone chrome (relief NONE)")
check('add_class("mp-slothit")' in slot_source,
      "the wrapper carries the mp-slothit class the stylesheet neutralises")
check("set_vexpand" in attrs,
      "the wrapper still vexpands, so the three meal rows share the window")
check("set_tooltip_text" in attrs,
      "the cell keeps its accessible name as a tooltip")
check("add" in attrs, "the slot content box is still the wrapper's child")

# -- the activation path -----------------------------------------------------

signals = [ast.literal_eval(n.args[0]) for n in calls if n.func.attr == "connect"
           and n.args and isinstance(n.args[0], ast.Constant)]
check(signals.count("clicked") == 1,
      "opening a slot uses the keyboard-aware clicked signal")
check("button-press-event" not in signals
      and "button-release-event" not in signals,
      "no raw pointer-press path survives beside the clicked handler")

connect = next((n for n in calls if n.func.attr == "connect" and len(n.args) > 1
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "clicked"), None)
handler = connect.args[1] if connect is not None else None
check(isinstance(handler, ast.Lambda),
      "the clicked handler is a per-cell callable")

# A cell must carry its OWN day and meal. All 21 handlers are made inside one
# rebuild loop, so a lambda that read `day`/`meal` from the enclosing scope
# rather than binding them as defaults would leave every cell in the week
# editing one and the same slot.
params = [a.arg for a in slot_widget.args.args]
check(params[:3] == ["self", "day", "meal"],
      "_slot_widget still takes the day and meal it is drawing")
bound = {}
if isinstance(handler, ast.Lambda):
    args, defaults = handler.args.args, handler.args.defaults
    for arg, default in zip(args[len(args) - len(defaults):], defaults):
        if isinstance(default, ast.Name):
            bound[default.id] = arg.arg
check(set(bound) == {"day", "meal"},
      "the handler binds this cell's own day AND meal as lambda defaults")
edit = next((n for n in ast.walk(handler) if isinstance(handler, ast.Lambda)
             and isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_edit_slot"), None)
check(edit is not None and len(edit.args) == 2
      and [a.id for a in edit.args if isinstance(a, ast.Name)]
      == [bound.get("day"), bound.get("meal")],
      "the handler passes that bound day and meal on to _edit_slot")

# -- what the swap must not have cost ----------------------------------------

check("self._cells[(day, meal)] = hit" in slot_source
      and slot_source.rstrip().endswith("return hit"),
      "the (day, meal) -> widget map still records the wrapper")
check('add_class("mp-slot")' in slot_source
      and 'add_class("today")' in slot_source
      and 'add_class("mp-dish")' in slot_source
      and 'add_class("mp-empty")' in slot_source,
      "the inner cell keeps its slot, today, dish and empty classes")
check("_edit_slot" in [n.name for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef)]
      and "dialog_prefill" in ast.get_source_segment(source,
                                                     method("_edit_slot")),
      "the dialog it opens, and its prefill rule, are untouched")

# The grid contract: seven days across, three meals down, and a widget built
# for every one of them -- 21 focusable cells, none skipped for being empty.
# An empty slot is the one you most need to be able to reach.
refresh = method("_refresh")
refresh_source = ast.get_source_segment(source, refresh) or ""
meals = next((n for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "MEALS"
                      for t in n.targets)), None)
n_meals = len(meals.value.elts) if meals is not None else 0
n_days = next((n.args[0].value for n in ast.walk(refresh)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "range" and len(n.args) == 1
               and isinstance(n.args[0], ast.Constant)), 0)
check(n_meals * n_days == 21,
      "the week is still %s days x %s meals = 21 cells" % (n_days, n_meals))
built = [n for n in ast.walk(refresh) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)
         and n.func.attr == "_slot_widget"]
check(len(built) == 1 and "for r, meal in enumerate(MEALS)" in refresh_source,
      "every cell in that grid comes from the one _slot_widget call")
check("if slot" not in refresh_source,
      "no cell is skipped for being empty")

# -- the stylesheet ----------------------------------------------------------

# Declarations only. A rule that merely TALKS about `outline: none` in a
# comment must not be able to trip -- or satisfy -- a check about what the
# stylesheet actually declares.
start = source.find('css = b"""')
raw_css = source[start:source.find('"""', start + 10)]
css = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.S)

check(start != -1 and all(ord(c) < 128 for c in raw_css),
      "the css literal stays ASCII (one stray byte kills the stylesheet)")
neutral = re.search(r"\.mp-slothit\s*\{[^}]*\}", css)
neutral = neutral.group(0) if neutral else ""
check(all(d in neutral for d in ("padding: 0", "margin: 0", "border: none",
                                 "background: transparent",
                                 "background-image: none", "box-shadow: none",
                                 "min-width: 0", "min-height: 0")),
      "mp-slothit is layout-neutral: no padding, border, fill or shadow")
check(re.search(r"\.mp-slothit:hover,[^{]*:active[^{]*\{[^}]*"
                r"background: transparent", css) is not None,
      "the button's OWN hover fill is suppressed, so only the cell reacts")

# Hover OWNERSHIP: the pointer sits on the wrapper, so the wrapper's state is
# what has to drive the fills inside it.
check(".mp-slothit:hover .mp-slot {" in css,
      "wrapper hover fills the ordinary cell")
check(".mp-slothit:hover .mp-slot.today {" in css,
      "wrapper hover fills today's cell with its own tint")
check(".mp-slothit:hover .mp-empty {" in css,
      'wrapper hover still brings up the quiet "Add" label')
check(".mp-slot:hover" not in css and ".mp-slot.today:hover" not in css,
      "no orphaned .mp-slot hover rule the pointer can no longer reach")

# The base look is the part that must NOT have changed.
check(re.search(r"\.mp-slot\s*\{[^}]*border:[^}]*\}", css) is not None
      and re.search(r"\.mp-slot\s*\{[^}]*padding:[^}]*\}", css) is not None,
      "the cell keeps its own border and padding")
check(re.search(r"\.mp-slot\.today\s*\{[^}]*background:[^}]*\}", css)
      is not None,
      "today's cell keeps its base tint")
check(".mp-empty {" in css and ".mp-dish {" in css,
      "the dish and empty labels keep their base type")

check(":focus" not in css and "outline" not in css,
      "app CSS does not suppress the global keyboard focus indicator")

print("\n%d failed" % len(fails))
print("RESULT: %s" % ("FAILED" if fails else "PASS"))
sys.exit(1 if fails else 0)
