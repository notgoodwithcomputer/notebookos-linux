#!/usr/bin/env python3
"""Cookbook startup must bound its text-only recovery store."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/cookbook.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "CookbookStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_cookbook_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "COOKBOOK_FILE": "unused",
         "MAX_COOKBOOK_BYTES": 8 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_cookbook_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"recipes": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"recipes": []}
    scope["COOKBOOK_FILE"] = fh.name
    assert read_store(limit=32) == {"recipes": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["CookbookStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized cookbook store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except CookbookStoreTooLarge:" in source
assert "self._quarantine_pending = True" in source
print("PASS oversized cookbook state is bounded and marked for preservation")
print("RESULT: ALL PASS")
