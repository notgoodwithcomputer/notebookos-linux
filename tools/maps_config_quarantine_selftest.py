#!/usr/bin/env python3
"""Headless regression for Maps refusing to overwrite unquarantined config."""
import ast
import json
import os
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/maps.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
methods = {node.name: node for node in ast.walk(tree)
           if isinstance(node, ast.FunctionDef)
           and node.name in ("_load_cfg", "_save_cfg")}

tmp = tempfile.mkdtemp(prefix="maps-config-")
cfg = os.path.join(tmp, "maps.json")
foreign = ["view data from another version"]
with open(cfg, "w", encoding="utf-8") as fh:
    json.dump(foreign, fh)

writes = []
nbapp = types.SimpleNamespace(
    quarantine_unrecognized=lambda _path: None,
    atomic_write_json=lambda path, data: writes.append((path, data)),
    note_save_failure=lambda *_args: None,
)
namespace = {"json": json, "os": os, "nbapp": nbapp}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=list(methods.values()), type_ignores=[])), PATH, "exec"),
     namespace)


class FakeMaps:
    _load_cfg = namespace["_load_cfg"]
    _save_cfg = namespace["_save_cfg"]

    def __init__(self):
        self.pack = types.SimpleNamespace(path="/maps/region.nbm2")
        self.cx = self.cy = 0.0
        self.scale = 1000.0

    def _cfg_path(self):
        return cfg


app = FakeMaps()
assert app._load_cfg() == {}
assert app._cfg_writable is False
app._save_cfg()
assert writes == []
with open(cfg, encoding="utf-8") as fh:
    assert json.load(fh) == foreign
print("PASS Maps preserves a foreign config when quarantine cannot move it")
print("RESULT: PASS")
