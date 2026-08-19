#!/usr/bin/env python3
"""2048 launch must not ingest an unbounded damaged state store."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/g2048.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_state_json"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "STATE_FILE": "unused", "MAX_STATE_BYTES": 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
load = scope["_load_state_json"]


with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"score": 12}')
    fh.flush()
    assert load(fh.name, 32) == {"score": 12}
    scope["STATE_FILE"] = fh.name
    assert load(limit=32) == {"score": 12}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        load(fh.name, 32)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized state was accepted")

print("PASS 2048 accepts bounded state and rejects overflow before JSON parsing")
print("RESULT: ALL PASS")
