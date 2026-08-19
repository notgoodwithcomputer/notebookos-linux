#!/usr/bin/env python3
"""The image-only system menu button must expose its purpose."""
import ast
from pathlib import Path

p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/shell.py")
method = next(n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "_menu_button")
attrs = {n.func.attr for n in ast.walk(method) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)}
assert {"set_tooltip_text", "set_name"} <= attrs
print("PASS the logo system-menu button has pointer and assistive labels")
print("RESULT: PASS")
