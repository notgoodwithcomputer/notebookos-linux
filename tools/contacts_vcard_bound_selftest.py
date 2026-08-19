#!/usr/bin/env python3
"""Contacts must bound selected vCard input before parsing it."""

import ast
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/contacts.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "read_vcard_text")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_VCARD_BYTES": 32 * 1024 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_vcard_text = scope["read_vcard_text"]


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "contacts.vcf"
    path.write_bytes(b"\xef\xbb\xbfBEGIN:VCARD\nEND:VCARD\n")
    assert read_vcard_text(path, 64) == "BEGIN:VCARD\nEND:VCARD\n"
    print("PASS ordinary vCard input and UTF-8 BOM still decode normally")

    path.write_bytes(b"x" * 65)
    try:
        read_vcard_text(path, 64)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized vCard was accepted")
    print("PASS oversized vCard input is rejected after a bounded read")

source = SOURCE.read_text(encoding="utf-8")
assert "parse_vcards(read_vcard_text(path))" in source
print("PASS the visible import action uses the bounded reader")
print("RESULT: ALL PASS")
