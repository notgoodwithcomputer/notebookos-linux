#!/usr/bin/env python3
"""Workout history loading must be bounded and fail closed."""

import ast
import copy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/workout.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "WorkoutStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_store_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"STORE": "unused", "MAX_STORE_BYTES": 16 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_store_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"log": {}}')
    fh.flush()
    assert read_store(fh.name, 32) == {"log": {}}
    scope["STORE"] = fh.name
    assert read_store(limit=32) == {"log": {}}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["WorkoutStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized workout history was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except WorkoutStoreTooLarge:" in source
assert "nbapp.quarantine_unrecognized(STORE)" in source
print("PASS oversized workout history is bounded and preserved before fallback")
print("RESULT: ALL PASS")
