#!/usr/bin/env python3
"""Headless rollback check for failed Academics destructive writes."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import academics  # noqa: E402


class Undo:
    def __init__(self): self.calls = []
    def checkpoint(self, label): self.calls.append(("checkpoint", label))
    def commit(self): self.calls.append(("commit", None))


class Probe:
    _copy_classes = staticmethod(academics.Academics._copy_classes)
    _copy_lectures = staticmethod(academics.Academics._copy_lectures)
    _undo_snapshot = academics.Academics._undo_snapshot
    _rollback_failed_save = academics.Academics._rollback_failed_save
    _remove_homework = academics.Academics._remove_homework
    def __init__(self):
        self.classes = []; self.lectures = []
        self.homework = [{"title": "Essay", "cls": -1, "due": "",
                          "done": False}]
        self.active = -1; self._sel_block = -1; self.undo = Undo()
    def _capture_active(self): pass
    def _caret_offset(self): return 0
    def _confirm(self, *_a, **_k): return True
    def _save_to_disk(self): return False
    def _refresh_sidebar(self): pass
    def _refresh_canvas(self): pass
    def _refresh_schedule(self): pass
    def _refresh_homework(self, **_k): pass


app = Probe(); before = copy.deepcopy(app.homework)
app._remove_homework(0)
ok = app.homework == before and app.undo.calls == [
    ("checkpoint", "Remove Assignment"), ("commit", None)]
print(("PASS " if ok else "FAIL ")
      + "failed assignment removal restores model without a history step")
print("RESULT: %s" % ("PASS" if ok else "FAILED"))
raise SystemExit(not ok)
