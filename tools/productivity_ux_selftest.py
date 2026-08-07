#!/usr/bin/env python3
"""Headless acceptance checks for the representative productivity UX pass."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import nbcommands  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def command_contract():
    command = nbcommands.get("edit.find")
    check(command is not None and command.shortcut == "Ctrl+F",
          "Find comes from the canonical command registry with Ctrl+F")
    calls = []
    item = nbcommands.item("edit.find", lambda: calls.append("find"))
    check("Find" in item[0] and "Ctrl+F" in item[0] and callable(item[1]),
          "registry adapter supplies the menu label and callback shape")


def contacts_wiring_contract():
    path = DE / "contacts.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    methods = {node.name: node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    check("_focus_search" in methods and "_clear_search" in methods,
          "Contacts exposes one focus and one immediate clear path")
    clear = ast.get_source_segment(source, methods["_clear_search"]) or ""
    key = ast.get_source_segment(source, methods["_on_key"]) or ""
    menu = ast.get_source_segment(source, methods["menu_items"]) or ""
    check("source_remove" in clear and "_rebuild_list()" in clear,
          "clearing search cancels debounce and rebuilds immediately")
    check("Gdk.KEY_Escape" in key and "self._clear_search()" in key and
          "return True" in key,
          "Escape consumes the local search layer before window handling")
    check("Gdk.KEY_f" in key and "self._focus_search()" in key,
          "Ctrl+F focuses search from anywhere in Contacts")
    check('nbcommands.item("edit.find"' in menu,
          "Contacts menu uses the canonical Find command")


if __name__ == "__main__":
    command_contract()
    contacts_wiring_contract()
    print("productivity UX selftest: OK")
