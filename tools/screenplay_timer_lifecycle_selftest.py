#!/usr/bin/env python3
"""Headless regression for Screenplay timers dispatched during close."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/screenplay.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
methods = {node.name: node for node in ast.walk(tree)
           if isinstance(node, ast.FunctionDef)
           and node.name in ("_count_tick", "_save_now")}
namespace = {"_t": lambda text: text}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=list(methods.values()), type_ignores=[])), PATH, "exec"),
     namespace)


class FakeScreenplay:
    _count_tick = namespace["_count_tick"]
    _save_now = namespace["_save_now"]

    def __init__(self, closed):
        self._closed = closed
        self._count_timer = 1
        self._save_timer = 2
        self.counts = 0
        self.saves = 0
        self.saved_marks = 0

    def _refresh_counts(self):
        self.counts += 1

    def _save_doc(self):
        self.saves += 1
        return True

    def _set_saved(self):
        self.saved_marks += 1


closed = FakeScreenplay(True)
assert closed._count_tick() is False and closed.counts == 0
assert closed._save_now() is False and closed.saves == closed.saved_marks == 0
assert closed._count_timer is None and closed._save_timer is None

open_app = FakeScreenplay(False)
assert open_app._count_tick() is False and open_app.counts == 1
assert open_app._save_now() is False
assert open_app.saves == 1 and open_app.saved_marks == 1
print("PASS Screenplay drops count/save callbacks dispatched after close")
print("RESULT: PASS")
