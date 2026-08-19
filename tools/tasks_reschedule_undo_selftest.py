#!/usr/bin/env python3
"""Display-free structural-history contract for task rescheduling."""
import copy
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="tasks-reschedule-"))
import tasks  # noqa: E402


class Undo:
    def __init__(self): self.events = []
    def checkpoint(self, label): self.events.append(("before", label))
    def commit(self): self.events.append(("after", None))


class Stand:
    _reschedule = tasks.Tasks._reschedule

    def __init__(self, row, succeeds=True):
        self.tasks = [copy.deepcopy(row)]
        self.undo = Undo()
        self.refreshes = 0
        self.succeeds = succeeds
    def _close_task_menu(self): pass
    def _undo_snapshot(self):
        return {"tasks": copy.deepcopy(self.tasks), "projects": [], "view": "v"}
    def _save_tasks_or_restore(self, before):
        if not self.succeeds:
            self.tasks = copy.deepcopy(before["tasks"])
            return False
        return True
    def _refresh(self): self.refreshes += 1


app = Stand({"due": "today", "date": "2026-08-15", "done": False})
app._reschedule(0, "tomorrow", "2026-08-16")
assert app.tasks[0]["date"] == "2026-08-16"
assert app.undo.events == [("before", "Reschedule Task"), ("after", None)]

failed = Stand({"due": "anytime", "date": "", "done": True}, succeeds=False)
failed._reschedule(0, "today", "2026-08-15")
assert failed.tasks == [{"due": "anytime", "date": "", "done": True}]
assert failed.undo.events == [("before", "Reschedule Task"), ("after", None)]

same = Stand({"due": "today", "date": "2026-08-15", "done": False})
same._reschedule(0, "today", "2026-08-15")
assert same.undo.events == [] and same.refreshes == 0
print("PASS reschedule brackets successful and rolled-back changes in history")
print("PASS a no-op reschedule creates no undo frame")
print("RESULT: PASS")
