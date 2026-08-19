#!/usr/bin/env python3
"""Deleting a calendar asks before removing it and its events."""
import ast
import copy
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot", "board", "notebookos",
                    "rootfs-overlay", "opt", "notebook", "de", "calendar.py")
tree = ast.parse(open(PATH, encoding="utf-8").read(), filename=PATH)
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Calendar")
method = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "_on_delete_cal")
probe = ast.ClassDef(name="Probe", bases=[], keywords=[], body=[method],
                     decorator_list=[])
scope = {"_t": lambda s: s}
exec(compile(ast.fix_missing_locations(ast.Module(body=[probe], type_ignores=[])),
             PATH, "exec"), scope)


def run(answer):
    obj = scope["Probe"]()
    obj.calendars = [{"name": "Personal"}, {"name": "Work"}]
    obj.events = [{"cal": "Personal", "title": "Dentist"},
                  {"cal": "Work", "title": "Review"}]
    obj.cals_on = {"Personal": True, "Work": True, "Work_area": object()}
    calls = []
    obj._confirm = lambda *args: (calls.append(args), answer)[1]
    obj.undo = type("Undo", (), {
        "checkpoint": lambda _s, label: calls.append(("checkpoint", label)),
        "commit": lambda _s: calls.append(("commit",)),
    })()
    obj._save_calendars = lambda: calls.append(("save-calendars",))
    obj._save_events = lambda: calls.append(("save-events",))
    obj._populate_cal_list = lambda: calls.append(("populate",))
    obj._refresh = lambda: calls.append(("refresh",))
    before = copy.deepcopy((obj.calendars, obj.events))
    obj._on_delete_cal(None, "Work")
    return obj, calls, before


obj, calls, before = run(False)
assert (obj.calendars, obj.events) == before
assert calls == [("Delete Calendar", "Delete calendar “Work”?", "Delete")], calls

obj, calls, _before = run(True)
assert obj.calendars == [{"name": "Personal"}], obj.calendars
assert obj.events == [{"cal": "Personal", "title": "Dentist"}], obj.events
assert calls[0] == ("Delete Calendar", "Delete calendar “Work”?", "Delete")
assert ("checkpoint", "Delete Calendar") in calls and ("commit",) in calls

print("PASS: calendar deletion mutates data only after confirmation")
print("RESULT: PASS")
