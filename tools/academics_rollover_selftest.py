#!/usr/bin/env python3
"""Headless regression for Academics homework/schedule date rollover."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/academics.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_check_day_rollover")

day = ["2026-08-15"]
namespace = {"_today_key": lambda: day[0]}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeAcademics:
    _check_day_rollover = namespace["_check_day_rollover"]

    def __init__(self):
        self._closed = False
        self._shown_day = day[0]
        self.schedule_refreshes = 0
        self.homework_refreshes = 0

    def _refresh_schedule(self):
        self.schedule_refreshes += 1

    def _refresh_homework(self):
        self.homework_refreshes += 1


app = FakeAcademics()
assert app._check_day_rollover() is True
assert (app.schedule_refreshes, app.homework_refreshes) == (0, 0)
day[0] = "2026-08-16"
assert app._check_day_rollover() is True
assert app._shown_day == day[0]
assert (app.schedule_refreshes, app.homework_refreshes) == (1, 1)
assert app._check_day_rollover() is True
assert (app.schedule_refreshes, app.homework_refreshes) == (1, 1)
app._closed = True
day[0] = "2026-08-17"
assert app._check_day_rollover() is False
assert (app.schedule_refreshes, app.homework_refreshes) == (1, 1)

assert '"_day_rollover_id")' in source
print("PASS Academics rebuckets dates once at rollover and stops after close")
print("RESULT: PASS")
