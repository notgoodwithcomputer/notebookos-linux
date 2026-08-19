#!/usr/bin/env python3
"""Headless regression for Novel timers already dispatched during close."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/novel.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
methods = {node.name: node for node in ast.walk(tree)
           if isinstance(node, ast.FunctionDef)
           and node.name in ("_mark_saved", "_pagestat_tick")}
namespace = {"time": __import__("time")}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=list(methods.values()), type_ignores=[])), PATH, "exec"),
     namespace)


class Label:
    def set_markup(self, _text):
        raise AssertionError("closed callback repainted the save label")


class FakeNovel:
    _mark_saved = namespace["_mark_saved"]
    _pagestat_tick = namespace["_pagestat_tick"]

    def __init__(self, closed):
        self._closed = closed
        self._save_timer = 1
        self._page_timer = 2
        self.save_lbl = Label()
        self.saves = 0
        self.pages = 0
        self.rearms = 0

    def _save_state(self):
        self.saves += 1
        return True

    def _refresh_pagestat(self):
        self.pages += 1

    def _arm_pagestat(self):
        self.rearms += 1


closed = FakeNovel(True)
assert closed._mark_saved() is False
assert closed._pagestat_tick() is False
assert (closed.saves, closed.pages, closed.rearms) == (0, 0, 0)
assert closed._save_timer is None and closed._page_timer is None

open_app = FakeNovel(False)
# Avoid exercising label markup; this regression is about the closed path.
open_app._pagestat_tick()
assert open_app.pages == 1
print("PASS Novel drops save/pagination callbacks dispatched after close")
print("RESULT: PASS")
