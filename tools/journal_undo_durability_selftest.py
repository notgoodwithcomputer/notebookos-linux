#!/usr/bin/env python3
"""Headless regression for rejected Journal undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import journal  # noqa: E402


class Buffer:
    def get_char_count(self): return 20
    def get_iter_at_offset(self, offset): return offset
    def place_cursor(self, where): self.caret = where


class Body:
    def __init__(self): self.buf = Buffer(); self.focuses = 0
    def get_buffer(self): return self.buf
    def grab_focus(self): self.focuses += 1


class Probe:
    _undo_restore = journal.Journal._undo_restore
    _copy_entries = staticmethod(journal.Journal._copy_entries)

    def __init__(self, save_ok):
        self.entries = [{"text": "current", "tags": []}]
        self.active = 0
        self.body = Body()
        self.save_ok = save_ok
        self.unsaved = 0

    def _undo_snapshot(self):
        return {"entries": copy.deepcopy(self.entries), "active": self.active,
                "_caret": 7}
    def _clear_search(self): pass
    def _refresh_list(self): pass
    # `top` mirrors the real signature: undo passes top=False so the page
    # keeps the reader's scroll position (see Journal._load_active).
    def _load_active(self, mark_saved=False, top=True): pass
    def _persist(self): return self.save_ok
    def _mark_unsaved(self): self.unsaved += 1
    def _mark_saved(self, have): pass


target = {"entries": [{"text": "older", "tags": []}], "active": 0,
          "_caret": 2}
failed = Probe(False)
before = copy.deepcopy(failed.entries)
succeeded = Probe(True)
checks = [
    (failed._undo_restore(target) is False and failed.entries == before,
     "failed undo restores the current journal entry"),
    (failed.unsaved == 1 and failed.body.buf.caret == 7,
     "failed undo retains the warning and current caret"),
    (succeeded._undo_restore(target) is True
     and succeeded.entries[0]["text"] == "older"
     and succeeded.body.buf.caret == 2,
     "successful undo restores and persists normally"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
