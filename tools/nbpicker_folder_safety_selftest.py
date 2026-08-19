#!/usr/bin/env python3
"""New Folder applies the path-containment check in every picker mode."""
import ast
from pathlib import Path

p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/nbpicker.py")
tree = ast.parse(p.read_text(encoding="utf-8"))
method = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_new_folder")
first = method.body[0]
assert isinstance(first, ast.If)
calls = [n for n in ast.walk(first.test) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)
         and n.func.attr == "_save_dir_safe"]
assert calls, "folder creation does not begin with the containment check"
assert not any(isinstance(n, ast.Attribute) and n.attr == "mode"
               for n in ast.walk(first.test)), "safety check is mode-dependent"
print("PASS Open and Save folder creation share the containment guard")
print("RESULT: PASS")
