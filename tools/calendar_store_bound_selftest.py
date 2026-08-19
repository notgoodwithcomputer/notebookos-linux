#!/usr/bin/env python3
"""Calendar recovery and merge reads must bound late external stores."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calendar.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "CalendarStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_store_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "MAX_STORE_BYTES": 32 * 1024 * 1024}
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
    except scope["CalendarStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized Calendar store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert source.count("_read_store_json(") >= 4
assert source.count("except CalendarStoreTooLarge:") == 3
merge = source[source.index("    def _read_events_file("):
               source.index("    def _merge_disk_events(")]
assert "self._events_quarantine = True" in merge
print("PASS Calendar launch and late merge reads are bounded and preserved")
print("RESULT: ALL PASS")
