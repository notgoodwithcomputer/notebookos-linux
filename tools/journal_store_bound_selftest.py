#!/usr/bin/env python3
"""Journal launch must bound and preserve the complete diary store."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/journal.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "JournalStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_journal_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "JOURNAL_FILE": "unused",
         "MAX_JOURNAL_BYTES": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_journal_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"entries": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"entries": []}
    scope["JOURNAL_FILE"] = fh.name
    assert read_store(limit=32) == {"entries": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["JournalStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized diary was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except JournalStoreTooLarge:" in source
assert "self._quarantine_pending = True" in source
print("PASS oversized journals are bounded and gated for preservation")
print("RESULT: ALL PASS")
