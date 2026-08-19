#!/usr/bin/env python3
"""Tasks split recovery stores must be bounded and fail closed independently."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/tasks.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "TasksStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_store_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "MAX_STORE_BYTES": 16 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_store_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b"[]")
    fh.flush()
    assert read_store(fh.name, 8) == []

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 9)
    fh.flush()
    try:
        read_store(fh.name, 8)
    except scope["TasksStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized Tasks store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert source.count("_read_store_json(") >= 3
assert "self._meta_quarantine_pending = True" in source
assert "self._flat_quarantine_pending = True" in source
assert "could not preserve the oversized Tasks projection" in source
assert source.index("self._last_external_ticks = self._merge_external_ticks(flat)") < \
       source.index("could not preserve the oversized Tasks projection") < \
       source.index("nbapp.atomic_write_json(TASKS_FILE, flat)")
print("PASS Tasks sidecar and flat projection are independently bounded")
print("RESULT: ALL PASS")
