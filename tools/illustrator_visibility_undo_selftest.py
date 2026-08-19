#!/usr/bin/env python3
"""Structural contract for undoable saved layer visibility."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/illustrator.py"


def calls(method, name):
    return any(isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                    or (isinstance(n.func, ast.Name) and n.func.id == name))
               for n in ast.walk(method))


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    failed = 0
    for name in ("_toggle_visible", "_show_all_layers"):
        method = methods[name]
        ok = calls(method, "_push") and calls(method, "_visibility_frame")
        print(("PASS: " if ok else "FAIL: ") + name + " banks visibility history")
        failed += not ok
    apply_src = ast.unparse(methods["_apply_frame"])
    ok = "frame[0] == 'vis'" in apply_src and "ly.visible = visible" in apply_src
    print(("PASS: " if ok else "FAIL: ") + "undo/redo restores saved eye states")
    failed += not ok
    show_src = ast.unparse(methods["_show_all_layers"])
    ok = "if not any" in show_src
    print(("PASS: " if ok else "FAIL: ") + "Show All is a no-op when already visible")
    failed += not ok
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
