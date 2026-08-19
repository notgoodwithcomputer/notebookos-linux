#!/usr/bin/env python3
"""Headless regression for failed Academics undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import academics  # noqa: E402


class Buffer:
    def get_char_count(self): return 100
    def get_iter_at_offset(self, offset): return offset
    def place_cursor(self, offset): self.caret = offset


class Body:
    def __init__(self): self.buf = Buffer(); self.focuses = 0
    def get_buffer(self): return self.buf
    def grab_focus(self): self.focuses += 1


class Probe:
    _undo_restore = academics.Academics._undo_restore
    _rollback_failed_save = academics.Academics._rollback_failed_save
    _copy_classes = staticmethod(academics.Academics._copy_classes)
    _copy_lectures = staticmethod(academics.Academics._copy_lectures)

    def __init__(self, save_results):
        self.classes = [{"id": "c1", "label": "Current", "meets": []}]
        self.lectures = [{"id": "l1", "title": "Current", "ranges": {}}]
        self.homework = [{"title": "Current"}]
        self.active = 0
        self._sel_block = -1
        self.body = Body()
        self.save_results = list(save_results)
        self.saves = 0

    def _undo_snapshot(self):
        return {"classes": copy.deepcopy(self.classes),
                "lectures": copy.deepcopy(self.lectures),
                "homework": copy.deepcopy(self.homework), "active": self.active,
                "_caret": 9}
    def _clear_search(self): pass
    def _refresh_sidebar(self): pass
    def _refresh_canvas(self): pass
    def _refresh_schedule(self): pass
    def _refresh_homework(self): pass
    def _save_to_disk(self):
        self.saves += 1
        return self.save_results.pop(0)


target = {"classes": [{"id": "c2", "label": "Older", "meets": []}],
          "lectures": [{"id": "l2", "title": "Older", "ranges": {}}],
          "homework": [{"title": "Older"}], "active": 0, "_caret": 2}
failed = Probe([False, True])
before = failed._undo_snapshot()
passed = Probe([True])
checks = [
    (failed._undo_restore(target) is False
     and failed.classes == before["classes"]
     and failed.lectures == before["lectures"]
     and failed.homework == before["homework"],
     "failed undo restores the complete academic term"),
    (failed.body.buf.caret == 9 and failed.saves == 2,
     "failed undo restores view caret and repairs disk best-effort"),
    (passed._undo_restore(target) is True
     and passed.classes[0]["label"] == "Older"
     and passed.body.buf.caret == 2,
     "successful undo persists and retains the restored view"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
