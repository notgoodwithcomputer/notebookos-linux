#!/usr/bin/env python3
"""Headless regression for failed Accounting undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import accounting  # noqa: E402


class Probe:
    _PAGE = accounting.Accounting._PAGE
    _undo_snapshot = accounting.Accounting._undo_snapshot
    _undo_restore = accounting.Accounting._undo_restore

    def __init__(self, saves):
        self.tx = [{"desc": "Current", "amt": 10.0}]
        self.opening = 25.0
        self._shown = 37
        self.saves = list(saves)
        self.save_calls = 0
        self.closed = []
        self.refreshes = 0
        self.flashed = []

    def _autosave(self):
        self.save_calls += 1
        return self.saves.pop(0)
    # A restore now clears the status line: it still held the sentence the
    # UNDONE action wrote ("Entry deleted" over a ledger with the entry back in
    # it). The stub grew this method with the app; without it the suite ended in
    # an AttributeError instead of a named result.
    def _flash(self, text, kind="info"): self.flashed.append(text)
    def _close_confirm(self): self.closed.append("confirm")
    def _close_edit(self): self.closed.append("edit")
    def _refresh(self): self.refreshes += 1


target = {"tx": [{"desc": "Older", "amt": 5.0}], "opening": 12.0}
failed = Probe([False, True])
before = copy.deepcopy(failed.tx)
passed = Probe([True])
checks = [
    (failed._undo_restore(target) is False and failed.tx == before
     and failed.opening == 25.0 and failed._shown == 37,
     "failed undo restores ledger, opening balance, and page"),
    (failed.save_calls == 2 and failed.closed == [],
     "failed undo repairs disk without closing active overlays"),
    (passed._undo_restore(target) is True
     and passed.tx[0]["desc"] == "Older" and passed.opening == 12.0
     and passed._shown == passed._PAGE,
     "successful undo commits and returns to the first page"),
    (passed.closed == ["confirm", "edit"],
     "successful undo closes obsolete editing overlays"),
    (passed.flashed == [""] and failed.flashed == [],
     "successful undo drops the status line the undone action left"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
