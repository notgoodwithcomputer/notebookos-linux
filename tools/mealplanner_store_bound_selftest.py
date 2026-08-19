#!/usr/bin/env python3
"""Meal Planner must bound plan/Cookbook reads and preserve oversized plans."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/mealplanner.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "MealStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_json_bounded"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "MAX_STORE_BYTES": 8 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_json_bounded"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"plan": {}}')
    fh.flush()
    assert read_store(fh.name, 32) == {"plan": {}}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["MealStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized meal store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert source.count("_read_json_bounded(path)") == 4
assert "except MealStoreTooLarge:\n        return True" in source
print("PASS plan, raw-store, shape, and Cookbook reads share a bounded loader")
print("RESULT: ALL PASS")
