#!/usr/bin/env python3
"""Academics exports must publish the rendered PDF atomically."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/academics.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Academics")
fn = next(n for n in cls.body
          if isinstance(n, ast.FunctionDef) and n.name == "_export_pdf")

atomic_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "nbapp"
                and n.func.attr == "atomic_write_via"]
assert len(atomic_calls) == 1
call = atomic_calls[0]
assert len(call.args) == 2
assert isinstance(call.args[1], ast.Name) and call.args[1].id == "render"
print("PASS every Academics export target is published through atomic_write_via")

direct = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
          and isinstance(n.func, ast.Name) and n.func.id == "render"]
assert not direct
print("PASS Academics never renders incrementally onto the destination PDF")

# Reaching here means every assert above held (a failure raises -> rc=1).
print("RESULT: ALL PASS")
