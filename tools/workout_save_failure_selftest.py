#!/usr/bin/env python3
"""Headless regression for Workout's persistence commit boundary."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import workout  # noqa: E402


class FakeUndo:
    """Just enough history to record the pair. Logging a set is a named undo
    step now (Ctrl+Z after logging takes back ONE set instead of silently
    discarding the day), so the probe has to own a history like the app does."""
    def __init__(self): self.calls = []
    def checkpoint(self, label=None): self.calls.append(("checkpoint", label))
    def commit(self): self.calls.append(("commit", None))


class Probe:
    _undo_snapshot = workout.Workout._undo_snapshot
    _save_or_rollback = workout.Workout._save_or_rollback
    _on_log = workout.Workout._on_log
    def __init__(self, save_ok):
        self.undo = FakeUndo()
        self.data = {"exercises": [{"id": "e1", "name": "Run",
                                    "sets": 3, "reps": 10}],
                     "log": {}, "goals": {}}
        self.sel = 0; self._save_error = ""; self.refreshes = 0
        self.save_ok = save_ok
    def _stamp_today_goal(self): pass
    def _save(self):
        if not self.save_ok: self._save_error = "Disk full"
        return self.save_ok
    def _refresh(self): self.refreshes += 1


real_today = workout.today_key
workout.today_key = lambda: "2026-08-15"
try:
    failed = Probe(False); before = copy.deepcopy(failed.data)
    failed._on_log(None, 0)
    passed = Probe(True); passed._on_log(None, 0)
finally:
    workout.today_key = real_today

checks = [
    (failed.data == before and failed._save_error == "Disk full",
     "failed set log is rolled back while its error remains visible"),
    (passed.data["log"]["2026-08-15"]["e1"] == [10],
     "successful set log commits normally"),
    (failed.undo.calls == [("checkpoint", "Log a Set"), ("commit", None)]
     and passed.undo.calls == [("checkpoint", "Log a Set"), ("commit", None)],
     "a logged set is one named undo step, saved or rolled back"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
