#!/usr/bin/env python3
"""Calculator expression inputs must share one bounded-size contract."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calculator.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name == "append_expression")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_EXPRESSION_CHARS": 256}
exec(compile(module, str(SOURCE), "exec"), scope)
append = scope["append_expression"]


assert append("1+", "2", limit=3) == "1+2"
assert append("1+2", "sin(", limit=3) == "1+2"
print("PASS keypad append accepts complete in-budget tokens and rejects overflow")

source = SOURCE.read_text(encoding="utf-8")
assert "entry.set_max_length(MAX_EXPRESSION_CHARS)" in source
assert "len(text) > MAX_EXPRESSION_CHARS" in source
assert "self.expr = append_expression(self.expr, v)" in source
assert "self.ys[i] = self.ys[i][:MAX_EXPRESSION_CHARS]" in source
print("PASS graph entries, clipboard paste, and keypad share the 256-char cap")
print("RESULT: ALL PASS")
