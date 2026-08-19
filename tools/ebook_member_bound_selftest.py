#!/usr/bin/env python3
"""EPUB parsing must reject pathological central-directory member counts."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/ebook.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name == "_epub_names_bounded")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"EPUB_MEMBER_MAX": 10000}
exec(compile(module, str(SOURCE), "exec"), scope)
bounded = scope["_epub_names_bounded"]


class Archive:
    def __init__(self, count):
        self.infos = [type("Info", (), {"filename": "f%d" % i})()
                      for i in range(count)]
    def infolist(self): return self.infos


assert bounded(Archive(3), limit=3) == {"f0", "f1", "f2"}
print("PASS ordinary EPUB member lists remain available")

try:
    bounded(Archive(4), limit=3)
except ValueError as exc:
    assert "too many" in str(exc)
else:
    raise AssertionError("oversized EPUB member list was accepted")
print("PASS pathological EPUB member counts are rejected before set allocation")

source = SOURCE.read_text(encoding="utf-8")
assert source.count("names = _epub_names_bounded(zf)") == 2
print("PASS content and metadata readers share the member-count gate")
print("RESULT: ALL PASS")
