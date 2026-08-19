#!/usr/bin/env python3
"""Headless regression: Export MIDI must not change Composer's save target."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/composer.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_export")
module = ast.Module(body=[method], type_ignores=[])
namespace = {}
exec(compile(ast.fix_missing_locations(module), PATH, "exec"), namespace)


class FakeComposer:
    _export = namespace["_export"]

    def __init__(self, result=True):
        self._path = "/documents/original.mid"
        self.result = result
        self.writes = []

    def _choose(self, save):
        assert save is True
        return "/documents/export.mid"

    def _write(self, path):
        self.writes.append(path)
        self._path = path  # mirror the real writer's successful binding
        return self.result


for result in (True, False):
    composer = FakeComposer(result)
    assert composer._export() is result
    assert composer.writes == ["/documents/export.mid"]
    assert composer._path == "/documents/original.mid", \
        "export must preserve the original Ctrl+S target"

cancelled = FakeComposer()
cancelled._choose = lambda _save: None
assert cancelled._export() is False
assert cancelled.writes == []
assert cancelled._path == "/documents/original.mid"
print("PASS Composer export preserves document identity")
print("RESULT: PASS")
