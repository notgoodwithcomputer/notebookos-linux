#!/usr/bin/env python3
"""Contacts launch must bound and preserve its address-book store."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/contacts.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "ContactsStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_contacts_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "CONTACTS_FILE": "unused",
         "MAX_CONTACTS_BYTES": 8 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_contacts_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"people": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"people": []}
    scope["CONTACTS_FILE"] = fh.name
    assert read_store(limit=32) == {"people": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["ContactsStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized address book was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except ContactsStoreTooLarge:" in source
assert "self._quarantine_pending = True" in source
print("PASS oversized address books are bounded and gated for preservation")
print("RESULT: ALL PASS")
