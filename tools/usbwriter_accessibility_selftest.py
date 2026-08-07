#!/usr/bin/env python3
"""Static contract for keyboard-operable USB Writer drive rows."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/usbwriter.py")
fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh: source = fh.read()
tree = ast.parse(source, SOURCE)
method = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_drive_row")
segment = ast.get_source_segment(source, method) or ""
check("Gtk.Button()" in segment and "Gtk.EventBox()" not in segment,
      "each drive choice is a real button")
check('connect("clicked"' in segment and "button-press-event" not in segment,
      "drive choice uses keyboard-aware clicked activation")
check("dd=d" in segment and "self._choose(dd)" in segment,
      "each handler binds its own drive record")
check("set_tooltip_text" in segment and 'd["label"]' in segment
      and 'd["size"]' in segment and 'd["node"]' in segment,
      "accessible tooltip repeats label, capacity, and device identity")
check(".uw-rowhit {" in source and "padding: 0" in source
      and "border: none" in source
      and ("background: transparent" in source
           or "background-color: transparent" in source),
      "button wrapper is visually and geometrically neutral")
check(".uw-rowhit:hover .uw-row" in source,
      "pointer hover still identifies the drive row")
check(".uw-rowhit:focus" not in source and "outline: none" not in source,
      "app CSS leaves the global focus indicator intact")

choose = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_choose")
choose_source = ast.get_source_segment(source, choose) or ""
check("if self.busy" in choose_source and "self.selected = d" in choose_source,
      "the busy guard and selected-drive model remain authoritative")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
