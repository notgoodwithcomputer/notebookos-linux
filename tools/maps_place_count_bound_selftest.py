#!/usr/bin/env python3
"""Maps must reject pathological place counts before reading/decompression."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/maps.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "NBM2")
fn = copy.deepcopy(next(n for n in cls.body
                        if isinstance(n, ast.FunctionDef) and n.name == "places"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"PACK_PLACES_COUNT_MAX": 500000, "PACK_COMPRESSED_MAX": 32 << 20,
         "PACK_PLACES_MAX": 64 << 20,
         "_lzma_limited": lambda *_a: b""}
exec(compile(module, str(SOURCE), "exec"), scope)


class File:
    def __init__(self): self.seeks = 0; self.reads = 0
    def seek(self, *_): self.seeks += 1
    def read(self, *_): self.reads += 1; return b""


class Probe:
    places = scope["places"]
    def __init__(self, count):
        self._places = None; self.places_cnt = count
        self.places_zlen = 1; self.places_off = 0; self.payload_base = 0
        self.f = File()


huge = Probe(500001)
empty = Probe(0)
checks = [
    (huge.places() == [] and (huge.f.seeks, huge.f.reads) == (0, 0)
     and huge._places == [],
     "pathological place count is rejected before read/decompression"),
    (empty.places() == [] and empty.f.reads == 0,
     "an ordinary empty place index remains valid"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(bool(ok) for ok, _name in checks)
print("RESULT: %d checks, ALL PASS (%d/%d)"
      % (len(checks), passed, len(checks)))
raise SystemExit(passed != len(checks))
