#!/usr/bin/env python3
"""Static contract for a keyboard-operable Journal entries list.

The entries list is the only route back to a past entry. Built out of
EventBoxes it answered to the pointer alone: not focusable, not in the Tab
ring, invisible to assistive tech. These checks pin the row down as a real
button, and pin the CSS that keeps a button from looking like one.
"""
import ast
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/journal.py")
fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    source = fh.read()
tree = ast.parse(source, SOURCE)
method = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_entry_row")
method_source = ast.get_source_segment(source, method) or ""
calls = [n for n in ast.walk(method) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)]
gtk_builds = [n.func.attr for n in calls
              if isinstance(n.func.value, ast.Name) and n.func.value.id == "Gtk"]

check(gtk_builds.count("Button") == 1,
      "each entry row is built as a real button")
check("EventBox" not in gtk_builds,
      "entry rows use no pointer-only EventBox")
check(any(n.func.attr == "set_relief" for n in calls),
      "the row button keeps neutral Papertone chrome")

signals = [ast.literal_eval(n.args[0]) for n in calls if n.func.attr == "connect"
           and n.args and isinstance(n.args[0], ast.Constant)]
check(signals.count("clicked") == 1,
      "selecting an entry uses the keyboard-aware clicked signal")
check("button-press-event" not in signals and "button-release-event" not in signals,
      "no raw pointer-press path survives beside the clicked handler")

# The handler has to close over this row's own index, not over whatever the
# rebuild loop happened to leave behind -- otherwise every row opens one entry.
handler = next((n for n in ast.walk(method) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "connect" and len(n.args) > 1), None)
check(handler is not None and isinstance(handler.args[1], ast.Lambda),
      "the clicked handler is a per-row callable")
bound = [a.arg for a in method.args.args if a.arg != "self"]
check(handler is not None
      and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "select_entry" and len(n.args) == 1
              and isinstance(n.args[0], ast.Name) and n.args[0].id == bound[0]
              for n in ast.walk(handler.args[1])),
      "the handler passes this row's own index to select_entry")

# Behaviour the swap must not have cost.
check("_active_title_lbl" in method_source
      and "_active_preview_lbl" in method_source,
      "the active row still hands its labels to the autosave refresh")
check('en["day"]' in method_source and 'en["wd"]' in method_source
      and 'en["title"]' in method_source and 'en["preview"]' in method_source,
      "the date box and title/preview meta are still built per row")
check('add_class("entryrowhit")' in method_source
      and 'add_class("entryrow")' in method_source
      and 'add_class("active")' in method_source,
      "the hit area, inner row and selected marking keep their classes")

# Declarations only. A rule that merely TALKS about `outline: none` in a
# comment must not be able to trip -- or satisfy -- a check about what the
# stylesheet actually declares.
css = re.sub(r"/\*.*?\*/", "", source[source.find('css = b"""'):], flags=re.S)
base = css[css.find(".entryrowhit {"):css.find(".entryrowhit:hover")]
check(".entryrowhit {" in css,
      "the row button has a base rule to neutralize theme button chrome")
check(all(rule in base for rule in (
    "padding: 0", "margin: 0", "border: none", "background: transparent",
    "background-image: none", "box-shadow: none")),
      "the base rule strips the theme's fill, border, padding and shadow")
check("min-height: 0" in base and "min-width: 0" in base,
      "the button imposes no minimum size of its own on the row")
check(".entryrowhit:hover" in css and "background: #EAE3D2" in css,
      "pointer hover still tints the row")
check(".entryrow.active" in css and "border-left: 3px solid #C8341E" in css,
      "the selected inner row keeps its fill and accent edge")
check(".entryrowhit:focus" not in css and "outline: none" not in css
      and "outline: 0" not in css,
      "app CSS does not suppress the global keyboard focus indicator")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
