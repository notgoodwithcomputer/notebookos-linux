#!/usr/bin/env python3
"""Static contract for keyboard-operable GBA cartridge cards."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/gbaemu.py")
fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    source = fh.read()
tree = ast.parse(source, SOURCE)
method = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_rom_card")
calls = [n for n in ast.walk(method) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)]
gtk_builds = [n.func.attr for n in calls
              if isinstance(n.func.value, ast.Name) and n.func.value.id == "Gtk"]
check("Button" in gtk_builds, "each cartridge is built as a real button")
check("EventBox" not in gtk_builds, "cartridge cards use no pointer-only EventBox")
signals = [ast.literal_eval(n.args[0]) for n in calls if n.func.attr == "connect"
           and n.args and isinstance(n.args[0], ast.Constant)]
check(signals.count("clicked") == 1 and "button-press-event" not in signals,
      "cartridge launch uses the keyboard-aware clicked signal")
check(any(n.func.attr == "set_tooltip_text" for n in calls),
      "the launch action retains its accessible tooltip name")
check(any(n.func.attr == "set_relief" for n in calls),
      "the button keeps neutral Papertone chrome")
method_source = ast.get_source_segment(source, method) or ""
check('p=m["path"]' in method_source,
      "each clicked handler binds its own ROM path")

css = source[source.find('css = b"""'):]
check(".rombutton" in css and "padding: 0" in css
      and "background: transparent" in css and "border: none" in css,
      "rombutton CSS is layout-neutral and transparent")
check(".rombutton:hover .romart" in css,
      "pointer hover still highlights the cartridge artwork")
check(".rombutton:focus" not in css and "outline: none" not in css,
      "app CSS does not suppress the global keyboard focus indicator")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
