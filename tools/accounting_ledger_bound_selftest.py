#!/usr/bin/env python3
"""Accounting launch and salvage must not ingest an unbounded ledger."""

import ast
import copy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/accounting.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "LedgerTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_ledger_text"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"TX_FILE": "unused", "MAX_LEDGER_BYTES": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_text = scope["_read_ledger_text"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"tx": []}')
    fh.flush()
    assert read_text(fh.name, 32) == '{"tx": []}'
    scope["TX_FILE"] = fh.name
    assert read_text(limit=32) == '{"tx": []}'

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_text(fh.name, 32)
    except scope["LedgerTooLarge"]:
        pass
    else:
        raise AssertionError("oversized ledger entered salvage parsing")

source = SOURCE.read_text(encoding="utf-8")
assert "except LedgerTooLarge:" in source
assert 'st["quarantine"] = True' in source
print("PASS oversized ledgers bypass salvage and require preservation before save")
print("RESULT: ALL PASS")
