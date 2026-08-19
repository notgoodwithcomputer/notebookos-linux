#!/usr/bin/env python3
"""Headless regression for Meal Planner's date rollover refresh."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/mealplanner.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_check_day_rollover")

day = ["2026-08-16"]                 # Sunday
weeks = {"2026-08-16": 100, "2026-08-17": 107}
namespace = {"_today_key": lambda: day[0],
             "_week_start": lambda key: weeks[key]}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakePlanner:
    _check_day_rollover = namespace["_check_day_rollover"]

    def __init__(self):
        self._closed = False
        self._shown_day = day[0]
        self.week = weeks[day[0]]
        self.refreshes = 0

    def _refresh(self):
        self.refreshes += 1


app = FakePlanner()
assert app._check_day_rollover() is True and app.refreshes == 0
day[0] = "2026-08-16"
assert app._check_day_rollover() is True and app.refreshes == 0
day[0] = "2026-08-17"
assert app._check_day_rollover() is True and app.refreshes == 1
assert app.week == 107, "Sunday's current week must advance at Monday midnight"
assert app._check_day_rollover() is True and app.refreshes == 1

browsed = FakePlanner()
browsed._shown_day = "2026-08-16"
browsed.week = 86
assert browsed._check_day_rollover() is True and browsed.refreshes == 1
assert browsed.week == 86, "rollover must not pull a browsed week back to today"

app._closed = True
weeks["2026-08-18"] = 107
day[0] = "2026-08-18"
assert app._check_day_rollover() is False and app.refreshes == 1
print("PASS Meal Planner follows the current week across Sunday midnight")
print("PASS Meal Planner preserves a deliberately browsed week")
print("RESULT: PASS")
