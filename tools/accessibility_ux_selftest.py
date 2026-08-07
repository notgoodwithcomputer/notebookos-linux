#!/usr/bin/env python3
"""Display-free conformance checks for shared accessibility contracts."""

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
THEME = (ROOT / "buildroot/board/notebookos/rootfs-overlay/usr/share/themes/"
         "Papertone/gtk-3.0/gtk.css")
sys.path.insert(0, str(DE))

import nbmotion  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def focus_and_state_contract():
    css = THEME.read_text(encoding="utf-8")
    check(all(rule in css for rule in (
        "outline-color: @accent", "outline-style: solid",
        "outline-width: 2px", "outline-offset: -3px")),
        "Papertone defines a visible keyboard focus ring, not color alone")
    check("entry:focus" in css and "border-color: @accent" in css,
          "text entry focus has a non-layout-shifting visible edge")
    default_block = re.search(r"button\.default\s*\{([^}]*)\}", css, re.S)
    declarations = (re.sub(r"/\*.*?\*/", "", default_block.group(1), flags=re.S)
                    if default_block is not None else "")
    check(default_block is not None and "outline: none" not in declarations,
          "default/safe dialog actions do not suppress keyboard focus")
    check("menuitem:disabled" in css and "@inkoff" in css,
          "disabled controls retain a distinct unavailable state")


def accessible_name_contract():
    path = DE / "nbapp.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)}
    check("name_control" in functions and "_name_hook" in functions,
          "shared explicit and tooltip-derived accessible naming APIs exist")
    hook = ast.get_source_segment(source, functions["_name_hook"]) or ""
    check("Gtk.Widget.set_tooltip_text" in hook and "acc.set_name(text)" in hook,
          "icon tooltips also become assistive-technology names")
    check("not (acc.get_name() or \"\").strip()" in hook,
          "the naming hook never overwrites a widget's explicit label")
    check("_name_hook()" in source,
          "accessible naming is installed for every app importing nbapp")
    check(all(token in source for token in
              (b.decode() for b in (b".dim", b".disabled", b".pipoff", b".chip-off"))),
          "high contrast excludes class-based unavailable states")


def reduced_motion_contract():
    old = nbmotion.reduced_motion()
    old_accel = os.environ.get("NB_ACCEL")
    try:
        os.environ["NB_ACCEL"] = "1"
        nbmotion.set_reduced_motion(True)
        check(nbmotion.policy(200) == 0,
              "Reduced Motion makes shared animations instant")
        nbmotion.set_reduced_motion(False)
        os.environ["NB_ACCEL"] = "0"
        check(nbmotion.policy(200) == 0,
              "software rendering also uses instant-equivalent transitions")
    finally:
        nbmotion.set_reduced_motion(old)
        if old_accel is None:
            os.environ.pop("NB_ACCEL", None)
        else:
            os.environ["NB_ACCEL"] = old_accel


if __name__ == "__main__":
    focus_and_state_contract()
    accessible_name_contract()
    reduced_motion_contract()
    print("accessibility UX selftest: OK")
