#!/usr/bin/env python3
"""Headless regression for rejected Meal Planner undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import mealplanner  # noqa: E402


class Probe:
    _undo_restore = mealplanner.MealPlanner._undo_restore

    def __init__(self, saves):
        self.plan = {"2026-08-15": {"dinner": {"kind": "note",
                                                "title": "Current"}}}
        self.saves = list(saves)
        self._save_error = ""
        self.save_calls = 0
        self.refreshes = 0

    def _save(self):
        self.save_calls += 1
        ok = self.saves.pop(0)
        self._save_error = "" if ok else "Disk full"
        return ok
    def _refresh(self): self.refreshes += 1


target = {"2026-08-15": {"dinner": {"kind": "note", "title": "Older"}}}
failed = Probe([False, True])
before = copy.deepcopy(failed.plan)
passed = Probe([True])
checks = [
    (failed._undo_restore(target) is False and failed.plan == before,
     "failed undo restores the current meal plan"),
    (failed.save_calls == 2 and failed._save_error == "Disk full"
     and failed.refreshes == 1,
     "failed undo repairs disk while retaining its visible error"),
    (passed._undo_restore(target) is True
     and passed.plan["2026-08-15"]["dinner"]["title"] == "Older"
     and passed.refreshes == 1,
     "successful undo persists and refreshes normally"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
