#!/usr/bin/env python3
"""Headless regression for rejected Novel undo recovery writes."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import novel  # noqa: E402


class Buffer:
    def __init__(self, text): self.text = text
    def get_char_count(self): return len(self.text)


class View:
    def __init__(self): self.buffer = Buffer("current manuscript")
    def get_buffer(self): return self.buffer


class Probe:
    _undo_restore = novel.Novel._undo_restore

    def __init__(self, saves):
        self.doc = {"title": "Current", "body": "current manuscript"}
        self.view = View()
        self.saves = list(saves)
        self._save_error = None
        self._recovery_dirty = False
        self.placed = []
        self.save_calls = 0

    def _undo_snapshot(self):
        return dict(copy.deepcopy(self.doc), _caret=9)
    def _restore(self, state):
        self.doc = {"title": state["title"], "body": state["body"]}
        self.view.buffer = Buffer(state["body"])
    def _init_counts(self): pass
    def _refresh_chapter_list(self): pass
    def _recount(self): pass
    def _place_caret_deferred(self, buf, caret):
        self.placed.append((buf.text, caret))
    def _save_state(self):
        self.save_calls += 1
        ok = self.saves.pop(0)
        self._save_error = None if ok else OSError("disk full")
        self._recovery_dirty = not ok
        return ok
    def _arm_pagestat(self): pass
    def _focus_editor(self): pass


target = {"title": "Older", "body": "older manuscript", "_caret": 3}
failed = Probe([False, True])
before = copy.deepcopy(failed.doc)
passed = Probe([True])
checks = [
    (failed._undo_restore(target) is False and failed.doc == before,
     "failed undo restores the complete current manuscript"),
    (failed.placed == [("current manuscript", 9)] and failed.save_calls == 2,
     "failed undo restores current caret and repairs recovery best-effort"),
    (isinstance(failed._save_error, OSError) and failed._recovery_dirty,
     "failed undo retains the visible recovery failure state"),
    (passed._undo_restore(target) is True and passed.doc["title"] == "Older"
     and passed.placed == [("older manuscript", 3)],
     "successful undo saves and restores its caret normally"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
