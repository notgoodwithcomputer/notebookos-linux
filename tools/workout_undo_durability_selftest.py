#!/usr/bin/env python3
"""Headless regression for failed Workout undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import nbapp  # noqa: E402
import workout  # noqa: E402


class Probe:
    _undo_snapshot = workout.Workout._undo_snapshot
    _restore_undo_snapshot = workout.Workout._restore_undo_snapshot

    def __init__(self):
        self.data = {"exercises": [], "log": {"today": {"e1": [10]}},
                     "goals": {}}
        self.sel = 0
        self._save_error = ""
        self.save_ok = True
        self.refreshes = 0

    def _save(self):
        if not self.save_ok:
            self._save_error = "Disk full"
        return self.save_ok

    def _refresh(self):
        self.refreshes += 1


app = Probe()
history = nbapp.UndoHistory(app._undo_snapshot, app._restore_undo_snapshot)
history.reset()
history.checkpoint("Log Set")
app.data["log"]["today"]["e1"].append(10)
history.commit()
visible = copy.deepcopy(app.data)

app.save_ok = False
failed = history.undo()
checks = [
    (failed is False, "failed persistence rejects undo"),
    (app.data == visible and app._save_error == "Disk full",
     "failed undo restores the visible workout and keeps the error"),
    (history.can_undo() and not history.can_redo(),
     "failed undo keeps the history cursor on the visible state"),
]

app.save_ok = True
checks.extend([
    (history.undo() and app.data["log"]["today"]["e1"] == [10],
     "undo succeeds after persistence recovers"),
    (history.can_redo(), "successful undo exposes redo"),
])

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
