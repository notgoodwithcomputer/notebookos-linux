#!/usr/bin/env python3
"""Headless regression for E-book Reader's final position capture."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/ebook.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_on_destroy")
namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class Generation:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


class FakeReader:
    _on_destroy = namespace["_on_destroy"]

    def __init__(self):
        self._closed = False
        self._nav = Generation()
        self.captures = 0

    def _remember_pos(self, force=False):
        assert force is True
        self.captures += 1


reader = FakeReader()
assert reader._on_destroy() is False
assert reader._on_destroy() is False
assert reader._closed is True
assert reader.captures == 1, "a torn-down scroller must not overwrite position"
assert reader._nav.closes == 1
print("PASS E-book Reader captures final reading position exactly once")
print("RESULT: PASS")
