#!/usr/bin/env python3
"""Calculator state/history recovery must bound valid JSON."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calculator.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "CalculatorStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_state_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "STATE_FILE": "unused",
         "MAX_STATE_BYTES": 8 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_state_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"tape": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"tape": []}
    scope["STATE_FILE"] = fh.name
    assert read_store(limit=32) == {"tape": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["CalculatorStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized calculator state was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except CalculatorStoreTooLarge:" in source
assert "nbapp.quarantine_unrecognized(STATE_FILE)" in source
print("PASS oversized Calculator state is bounded and preservation-gated")
print("RESULT: ALL PASS")
