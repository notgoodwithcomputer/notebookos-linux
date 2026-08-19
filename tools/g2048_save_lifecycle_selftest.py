#!/usr/bin/env python3
"""Headless regression for a 2048 save callback dispatched during close."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/g2048.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_flush_save")
namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeGame:
    _flush_save = namespace["_flush_save"]

    def __init__(self, closed):
        self._closed = closed
        self._save_timer = 8
        self.saves = 0

    def _save_best(self):
        self.saves += 1


closed = FakeGame(True)
assert closed._flush_save() is False
assert closed._save_timer is None and closed.saves == 0

open_game = FakeGame(False)
assert open_game._flush_save() is False
assert open_game._save_timer is None and open_game.saves == 1
print("PASS 2048 drops a save callback dispatched after final close save")
print("RESULT: PASS")
