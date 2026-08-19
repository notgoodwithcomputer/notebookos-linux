#!/usr/bin/env python3
"""Writer Open must bound selected native and plain documents."""

import ast
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/writer.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "_read_document_bytes")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_DOCUMENT_BYTES": 64 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
reader = scope["_read_document_bytes"]


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "draft.writer"
    path.write_bytes(b'{"body":"hello","runs":[]}')
    assert reader(path, 64) == b'{"body":"hello","runs":[]}'
    print("PASS ordinary Writer document bytes read normally")

    path.write_bytes(b"x" * 65)
    try:
        reader(path, 64)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized Writer document was accepted")
    print("PASS oversized Writer document is rejected after a bounded read")

source = SOURCE.read_text(encoding="utf-8")
assert "source_bytes = _read_document_bytes(path)" in source
print("PASS native, text, and Markdown Open share the bounded reader")
print("RESULT: ALL PASS")
