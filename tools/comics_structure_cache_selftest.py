#!/usr/bin/env python3
"""Display-free regression for Comics' page-indexed caches."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/comics.py"


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    structure = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_structure"
    )
    calls = {
        (node.func.value.attr, node.func.attr)
        for node in ast.walk(structure)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
    }
    required = {("_thumb_cache", "clear"), ("_object_overlay", "clear")}
    missing = required - calls
    if missing:
        print("FAIL: structural page changes do not clear " + ", ".join(sorted(x[0] for x in missing)))
        return 1
    print("PASS: structural page changes clear every page-indexed render cache")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
