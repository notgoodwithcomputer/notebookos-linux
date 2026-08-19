#!/usr/bin/env python3
"""Video Editor must bound selected project JSON before parsing."""

import ast
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/video.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "read_project_json")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_PROJECT_BYTES": 64 * 1024 * 1024, "json": json}
exec(compile(module, str(SOURCE), "exec"), scope)
reader = scope["read_project_json"]


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "movie.json"
    path.write_bytes(b'\xef\xbb\xbf{"slots": []}')
    assert reader(path, 64) == {"slots": []}
    print("PASS ordinary Video project JSON and UTF-8 BOM decode normally")

    path.write_bytes(b" " * 65)
    try:
        reader(path, 64)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized Video project was parsed")
    print("PASS oversized Video project is rejected after a bounded read")

source = SOURCE.read_text(encoding="utf-8")
assert "data = read_project_json(path)" in source
print("PASS the visible Open Project action uses the bounded reader")
print("RESULT: ALL PASS")
