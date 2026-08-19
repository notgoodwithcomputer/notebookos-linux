#!/usr/bin/env python3
"""Headless regression for Media's deferred Finder-open callback."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/media.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_display_startup")
namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeMedia:
    _display_startup = namespace["_display_startup"]

    def __init__(self, closed):
        self._closed = closed
        self._startup_id = 5
        self.opened = []

    def _display(self, path):
        self.opened.append(path)


closed = FakeMedia(True)
assert closed._display_startup("/pictures/photo.png") is False
assert closed._startup_id == 0 and closed.opened == []

open_app = FakeMedia(False)
assert open_app._display_startup("/pictures/photo.png") is False
assert open_app._startup_id == 0
assert open_app.opened == ["/pictures/photo.png"]
print("PASS Media drops its deferred Finder open after close")
print("RESULT: PASS")
