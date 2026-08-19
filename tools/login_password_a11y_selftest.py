#!/usr/bin/env python3
"""The authentication entry must keep a stable accessible Password name."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGIN = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/login.py"

tree = ast.parse(LOGIN.read_text(encoding="utf-8"))
found = False
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        continue
    if node.func.attr != "set_name" or not node.args:
        continue
    arg = node.args[0]
    if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
            and arg.func.id == "_t" and arg.args
            and isinstance(arg.args[0], ast.Constant)
            and arg.args[0].value == "Password"):
        found = True
        break
assert found, "password entry has no explicit localized accessible name"
print("PASS password entry has a stable localized accessible name")
print("RESULT: PASS")
