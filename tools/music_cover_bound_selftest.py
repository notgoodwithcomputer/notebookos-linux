#!/usr/bin/env python3
"""Music must cap embedded artwork before mapping/copying its payload."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/music.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "_info_image")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)


class Gst:
    class MapFlags:
        READ = 1


scope = {"Gst": Gst, "MAX_EMBEDDED_COVER_BYTES": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
info_image = scope["_info_image"]


class Buffer:
    def __init__(self, data, claimed=None):
        self.data = data; self.claimed = len(data) if claimed is None else claimed
        self.maps = 0; self.unmaps = 0
    def get_size(self): return self.claimed
    def map(self, _flags):
        self.maps += 1
        return True, type("Map", (), {"data": self.data})()
    def unmap(self, _map): self.unmaps += 1


class Info:
    def __init__(self, buf): self.buf = buf
    def get_tags(self):
        buf = self.buf
        return type("Tags", (), {
            "get_tag_size": lambda _self, _key: 1,
            "get_sample_index": lambda _self, _key, _index:
                (True, type("Sample", (), {"get_buffer": lambda _s: buf})()),
        })()


normal = Buffer(b"small-cover")
assert info_image(Info(normal), limit=64) == b"small-cover"
assert (normal.maps, normal.unmaps) == (1, 1)
print("PASS ordinary embedded artwork maps and unmaps normally")

huge = Buffer(b"not copied", claimed=65)
assert info_image(Info(huge), limit=64) is None
assert (huge.maps, huge.unmaps) == (0, 0)
print("PASS oversized embedded artwork is rejected before map or byte copy")
print("RESULT: ALL PASS")
