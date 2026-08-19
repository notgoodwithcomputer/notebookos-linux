#!/usr/bin/env python3
"""Music playlist/cache recovery must bound valid external JSON."""

import ast
import copy
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/music.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = copy.deepcopy(next(node for node in tree.body
                         if isinstance(node, ast.ClassDef)
                         and node.name == "MusicStoreTooLarge"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_store_json"))
module = ast.Module(body=[cls, fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"json": json, "CFG_FILE": "unused",
         "MAX_STORE_BYTES": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_store = scope["_read_store_json"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b'{"playlists": []}')
    fh.flush()
    assert read_store(fh.name, 32) == {"playlists": []}
    scope["CFG_FILE"] = fh.name
    assert read_store(limit=32) == {"playlists": []}

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b" " * 33)
    fh.flush()
    try:
        read_store(fh.name, 32)
    except scope["MusicStoreTooLarge"]:
        pass
    else:
        raise AssertionError("oversized music store was parsed")

source = SOURCE.read_text(encoding="utf-8")
assert "except MusicStoreTooLarge:" in source
assert "nbapp.quarantine_unrecognized(CFG_FILE)" in source
assert "self._store_load_ok = not os.path.exists(CFG_FILE)" in source
print("PASS oversized Music state is bounded and preservation-gated")
print("RESULT: ALL PASS")
