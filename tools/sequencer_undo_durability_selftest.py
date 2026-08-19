#!/usr/bin/env python3
"""Sequencer Undo/Redo must not advance when recovery persistence fails."""

import ast
import copy
from pathlib import Path
import types


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/sequencer.py"


def load_method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Sequencer")
    fn = copy.deepcopy(next(n for n in cls.body
                            if isinstance(n, ast.FunctionDef) and n.name == name))
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"UNDO_DEPTH": 40}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope[name]


step_history = load_method("_step_history")


class Probe:
    _step_history = step_history

    def __init__(self, save_ok):
        self.transport = "stop"
        self.state = "current"
        self._path = "current.nseq"
        self._undo_stack = [{"state": "older", "path": "older.nseq"}]
        self._redo_stack = [{"state": "future", "path": "future.nseq"}]
        self._undo_names = ["Edit"]
        self._redo_names = ["Future"]
        self.save_ok = save_ok
        self.refreshes = 0

    def _arrangement(self):
        return {"state": self.state, "path": self._path}

    def _restore_arrangement(self, snap):
        self.state = snap["state"]
        self._path = snap["path"]

    def _save(self):
        return self.save_ok

    def _sync_controls(self): pass
    def _update_length_btn(self): pass
    def _update_proj(self): pass
    def refresh(self): self.refreshes += 1


failed = Probe(False)
before = (copy.deepcopy(failed._undo_stack), copy.deepcopy(failed._redo_stack),
          list(failed._undo_names), list(failed._redo_names))
assert failed._step_history(failed._undo_stack, failed._redo_stack,
                            failed._undo_names, failed._redo_names) is False
assert (failed.state, failed._path) == ("current", "current.nseq")
assert (failed._undo_stack, failed._redo_stack,
        failed._undo_names, failed._redo_names) == before
print("PASS failed Undo restores arrangement, identity, and both history cursors")

saved = Probe(True)
assert saved._step_history(saved._undo_stack, saved._redo_stack,
                           saved._undo_names, saved._redo_names) is True
assert (saved.state, saved._path) == ("older", "older.nseq")
assert not saved._undo_stack
assert saved._redo_stack[-1] == {"state": "current", "path": "current.nseq"}
assert saved._redo_names[-1] == "Edit"
print("PASS durable Undo advances normally and preserves the redo snapshot")
print("RESULT: PASS")
