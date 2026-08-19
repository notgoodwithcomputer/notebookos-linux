#!/usr/bin/env python3
"""Package inspection must not synchronously ingest unbounded source files."""

import ast
import copy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/packages.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_source_bounded"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_PACKAGE_SOURCE_BYTES": 2 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_source = scope["_read_source_bounded"]


with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as fh:
    fh.write('"""Small package."""\n')
    fh.flush()
    assert read_source(fh.name, 64).startswith('"""Small')

with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as fh:
    fh.write("x" * 65)
    fh.flush()
    try:
        read_source(fh.name, 64)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized package source was accepted")

print("PASS package source inspection accepts bounded input and rejects overflow")
print("RESULT: ALL PASS")
