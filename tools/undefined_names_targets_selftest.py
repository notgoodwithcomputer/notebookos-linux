#!/usr/bin/env python3
"""Attribute/subscript assignment bases remain undefined-name loads."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "undefined_names_audit.py")
spec = importlib.util.spec_from_file_location("undefined_names_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

source = """
ghost.attr = 1
phantom[0] = 2
left, *rest = (1, 2, 3)
print(left, rest)
counter += 1
declared: int
print(declared)
print(bound := 4)
print(bound)
"""
with tempfile.TemporaryDirectory(prefix="undefined-targets-") as td:
    path = os.path.join(td, "mutant.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    hits = dict(audit.audit_file(path))

assert hits["ghost"] == 2
assert hits["phantom"] == 3
assert "left" not in hits
assert "rest" not in hits
assert hits["counter"] == 6
assert hits["declared"] == 8
assert "bound" not in hits
assert len(hits) == 4

print("UNDEFINED NAMES TARGETS SELFTEST: 9 checks, all pass")
print("RESULT: ALL PASS")
