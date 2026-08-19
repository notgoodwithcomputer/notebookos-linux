#!/usr/bin/env python3
"""Illustrator must reject PNG bombs before Cairo surface allocation."""

import ast
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/illustrator.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name == "validate_png_input")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"os": os, "MAX_OPEN_PNG_BYTES": 128 * 1024 * 1024,
         "MAX_OPEN_PNG_PIXELS": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
validate = scope["validate_png_input"]


def header(width, height):
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big"))


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "drawing.png"
    path.write_bytes(header(2048, 2048))
    assert validate(path, max_bytes=64, max_pixels=2048 * 2048)
    print("PASS ordinary PNG dimensions pass before decode")

    path.write_bytes(header(100000, 100000))
    assert not validate(path, max_bytes=64, max_pixels=2048 * 2048)
    print("PASS decompression-bomb dimensions are rejected before Cairo")

    path.write_bytes(header(1, 1) + b"x" * 41)
    assert not validate(path, max_bytes=64, max_pixels=2048 * 2048)
    print("PASS oversized compressed input is rejected before decode")

source = SOURCE.read_text(encoding="utf-8")
assert "if not validate_png_input(path):" in source
assert source.index("if not validate_png_input(path):") < source.index(
    "cairo.ImageSurface.create_from_png(path)")
print("PASS visible Open validates the PNG before surface allocation")
print("RESULT: ALL PASS")
