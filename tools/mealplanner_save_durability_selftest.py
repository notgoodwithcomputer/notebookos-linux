#!/usr/bin/env python3
"""Headless checks that failed Meal Planner writes do not change the UI."""
import copy
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import mealplanner  # noqa: E402


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def app_with(plan):
    app = mealplanner.MealPlanner.__new__(mealplanner.MealPlanner)
    app.plan = copy.deepcopy(plan)
    app.undo = UndoProbe()
    app._save = lambda: False
    app._refresh = lambda: None
    app._confirm = lambda *_args: True
    return app


day = "2026-08-10"
original = {day: {"dinner": {"kind": mealplanner.KIND_NOTE,
                              "title": "Soup"}}}

app = app_with(original)
app._set_slot(day, "dinner", mealplanner.KIND_NOTE, "Stew")
assert app.plan == original, app.plan
assert app.undo.calls == [("checkpoint", "Edit Meal")], app.undo.calls
print("PASS failed meal edit restores the last durable plan")

app = app_with(original)
app.week = mealplanner._week_start(day)
app._clear_week()
assert app.plan == original, app.plan
assert app.undo.calls == [("checkpoint", "Clear Week")], app.undo.calls
print("PASS failed week clear restores the last durable plan")

app = app_with(original)
app._save = lambda: True
app._set_slot(day, "dinner", mealplanner.KIND_NOTE, "Stew")
assert app.plan[day]["dinner"]["title"] == "Stew", app.plan
assert app.undo.calls[-1] == ("commit", None), app.undo.calls
print("PASS successful edit remains visible and commits undo")
print("RESULT: PASS")
