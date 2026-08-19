#!/usr/bin/env python3
"""Screenplay Open must bound both JSON and plain selected files."""

import ast
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/screenplay.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "_read_script_bytes")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_SCRIPT_BYTES": 64 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
reader = scope["_read_script_bytes"]


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "draft.fountain"
    path.write_bytes(b"INT. ROOM - DAY\n")
    assert reader(path, 64) == b"INT. ROOM - DAY\n"
    print("PASS ordinary selected script bytes read normally")

    path.write_bytes(b"x" * 65)
    try:
        reader(path, 64)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized script was accepted")
    print("PASS oversized selected script is rejected after a bounded read")

source = SOURCE.read_text(encoding="utf-8")
assert "raw = _read_script_bytes(path)" in source
assert 'json.loads(_read_script_bytes(path).decode("utf-8-sig"))' in source
print("PASS plain-text and JSON Open paths share the bounded reader")
print("RESULT: ALL PASS")
