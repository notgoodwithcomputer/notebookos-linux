#!/usr/bin/env python3
"""Smooth touchpad scroll uses vertical direction and ignores horizontal."""
import ast
from pathlib import Path
p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/maps.py")
method = next(n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "_on_scroll")
text = ast.unparse(method)
assert "ScrollDirection.SMOOTH" in text and "dy < 0" in text
assert "if not ok or not dy" in text
assert "return False" in text
print("PASS smooth vertical direction controls zoom and zero/horizontal is ignored")
print("RESULT: PASS")
