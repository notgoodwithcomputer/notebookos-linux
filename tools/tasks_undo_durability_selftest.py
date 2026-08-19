#!/usr/bin/env python3
"""Headless regression for a rejected Tasks undo across its two stores."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import nbapp  # noqa: E402
import tasks  # noqa: E402


class Probe:
    _undo_snapshot = tasks.Tasks._undo_snapshot
    _restore_undo_snapshot = tasks.Tasks._restore_undo_snapshot

    def __init__(self):
        self.tasks = [{"title": "Pack", "done": False}]
        self.view = "view:today"
        self.save_ok = True
        self.saves = 0

    def _save_tasks(self):
        self.saves += 1
        return self.save_ok


old_projects = copy.deepcopy(tasks.PROJECTS)
try:
    app = Probe()
    history = nbapp.UndoHistory(app._undo_snapshot,
                                app._restore_undo_snapshot)
    history.reset()
    history.checkpoint("Complete Task")
    app.tasks[0]["done"] = True
    history.commit()
    visible = copy.deepcopy(app.tasks)

    app.save_ok = False
    rejected = history.undo()
    checks = [
        (rejected is False and app.tasks == visible,
         "failed undo leaves the visible task model unchanged"),
        (app.saves == 2,
         "failed rich-store undo attempts to restore the shared projection"),
        (history.can_undo() and not history.can_redo(),
         "rejected undo keeps the history cursor aligned with the screen"),
    ]
    app.save_ok = True
    checks.append(
        (history.undo() and app.tasks[0]["done"] is False,
         "undo succeeds normally after storage recovers"))
finally:
    tasks.PROJECTS[:] = old_projects
    tasks.PROJ_COLOR.clear()
    tasks.PROJ_COLOR.update(tasks.PROJECTS)

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
