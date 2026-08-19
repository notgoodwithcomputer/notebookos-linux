#!/usr/bin/env python3
"""Bills app and widget store reads must share a safe size boundary."""

import ast
import copy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/bills.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "BillsStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_store_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"STORE": "unused", "MAX_STORE_BYTES": 8 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_store_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"bills": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"bills": []}
    scope["STORE"] = fh.name
    assert read_store(limit=32) == {"bills": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["BillsStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized bill store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "raw = _read_store_json(path)" in source
assert "except BillsStoreTooLarge:" in source
assert "nbapp.quarantine_unrecognized(STORE)" in source
print("PASS app and widget bill reads are bounded with preservation wiring")
print("RESULT: ALL PASS")
