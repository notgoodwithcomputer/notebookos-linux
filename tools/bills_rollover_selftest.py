#!/usr/bin/env python3
"""Headless regression for Bill Tracker's midnight deadline refresh."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/bills.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_check_day_rollover")

day = ["2026-08-15"]
namespace = {"today_key": lambda: day[0]}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeBills:
    _check_day_rollover = namespace["_check_day_rollover"]

    def __init__(self):
        self._closed = False
        self._shown_day = day[0]
        self.refreshes = 0

    def _refresh(self):
        self.refreshes += 1


app = FakeBills()
assert app._check_day_rollover() is True
assert app.refreshes == 0, "same-day polling must not rebuild the interface"
day[0] = "2026-08-16"
assert app._check_day_rollover() is True
assert app._shown_day == "2026-08-16" and app.refreshes == 1
assert app._check_day_rollover() is True and app.refreshes == 1
app._closed = True
day[0] = "2026-08-17"
assert app._check_day_rollover() is False and app.refreshes == 1

assert "GLib.timeout_add_seconds(\n            30, self._check_day_rollover)" in source
assert "GLib.source_remove(rollover_id)" in source
print("PASS Bill Tracker refreshes once at date rollover and stops on close")
print("RESULT: PASS")
