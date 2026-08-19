#!/usr/bin/env python3
"""Novel New/Open use undo instead of redundant destructive prompts."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/novel.py"
tree = ast.parse(SRC.read_text())


def fn(name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def calls(name, callee):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == callee for n in ast.walk(fn(name)))


checks = [
    (not calls("_on_file_new", "_confirm"), "New has no redundant prompt"),
    (not calls("_do_open_path", "_confirm"), "Open has no redundant prompt"),
    (calls("_do_file_new", "checkpoint") and calls("_do_file_new", "commit"),
     "New remains a complete undo step"),
    (calls("_do_open_path", "checkpoint") and calls("_do_open_path", "commit"),
     "Open remains a complete undo step"),
]
for ok, label in checks:
    print(("PASS" if ok else "FAIL") + ": " + label)
print("RESULT: %s" % ("ALL PASS" if all(ok for ok, _ in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _ in checks))
