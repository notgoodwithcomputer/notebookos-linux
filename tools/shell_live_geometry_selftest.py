#!/usr/bin/env python3
"""Static contract for the panel's live RandR geometry refresh."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/shell.py"


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    refresh = methods.get("_refresh_geometry")
    if refresh is None:
        print("FAIL: panel has no live geometry refresh")
        return 1
    attrs = {
        n.attr for n in ast.walk(refresh)
        if isinstance(n, ast.Attribute)
    }
    required = {
        "_menu_close", "set_size_request", "set_default_size", "move_resize",
        "_reserve_strut", "_apply_shape", "queue_resize",
    }
    missing = required - attrs
    if missing:
        print("FAIL: geometry refresh omits " + ", ".join(sorted(missing)))
        return 1
    text = SOURCE.read_text(encoding="utf-8")
    for signal in ("size-changed", "monitors-changed"):
        if signal not in text:
            print("FAIL: panel does not subscribe to " + signal)
            return 1
    print("PASS: live geometry refresh updates allocation, strut and shapes")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
