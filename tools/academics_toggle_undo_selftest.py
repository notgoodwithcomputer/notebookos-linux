#!/usr/bin/env python3
"""Assignment completion must be captured after the undo checkpoint."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/academics.py"
tree = ast.parse(SRC.read_text())
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_on_hw_toggle")
checkpoint = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "checkpoint")
writes = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
          and any(isinstance(t, ast.Subscript) for t in n.targets)]
rollback = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_rollback_failed_save" for n in ast.walk(fn))
ok = bool(writes) and checkpoint < min(writes) and rollback
print(("PASS" if ok else "FAIL") + ": toggle checkpoints before mutation and rolls back")
print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
raise SystemExit(not ok)
