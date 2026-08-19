#!/usr/bin/env python3
"""The emulator log dialog must bound and safely decode external output."""

import ast
import copy
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/gbaemu.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_log_tail"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"os": os, "MAX_EMULATOR_LOG_VIEW": 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_tail = scope["_read_log_tail"]


with tempfile.NamedTemporaryFile() as fh:
    fh.write(b"old failure\n" + b"x" * 80 + b"\xffnew failure\n")
    fh.flush()
    text = read_tail(fh.name, 16)
    assert "old failure" not in text
    assert text.endswith("new failure")
    assert "\ufffd" in text

print("PASS emulator log view reads a bounded tail and replaces invalid bytes")
print("RESULT: ALL PASS")
