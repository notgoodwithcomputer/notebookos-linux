#!/usr/bin/env python3
"""Headless regression for GBA Emulator's deferred first scan."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/gbaemu.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_first_scan")
namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeEmulator:
    _first_scan = namespace["_first_scan"]

    def __init__(self, closed):
        self._closed = closed
        self._scan_source = 9
        self.calls = []

    def _request_scan(self):
        self.calls.append("request")


closed = FakeEmulator(True)
assert closed._first_scan() is False
assert closed._scan_source == 0 and closed.calls == []

open_app = FakeEmulator(False)
assert open_app._first_scan() is False
assert open_app.calls == ["request"]
assert open_app._scan_source == 0
print("PASS GBA Emulator drops its deferred scan after close")
print("RESULT: PASS")
