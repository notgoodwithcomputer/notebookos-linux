#!/usr/bin/env python3
"""Video Editor history must not advance past a failed recovery write."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/video.py"


def method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "VideoEditor")
    fn = copy.deepcopy(next(n for n in cls.body
                            if isinstance(n, ast.FunctionDef) and n.name == name))
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"UNDO_DEPTH": 40}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope[name]


undo_action = method("_undo_action")
redo_action = method("_redo_action")


class Probe:
    _undo_action = undo_action
    _redo_action = redo_action

    def __init__(self, save_ok):
        self.state = "current"
        self._undo = ["older"]
        self._redo = ["future"]
        self._undo_names = ["Cut"]
        self._redo_names = ["Redo cut"]
        self.save_ok = save_ok

    def _snapshot(self):
        return self.state

    def _restore(self, snap, persist=True):
        self.state = snap
        return self.save_ok if persist else True


failed = Probe(False)
before = (list(failed._undo), list(failed._redo),
          list(failed._undo_names), list(failed._redo_names))
assert failed._undo_action() is False
assert failed.state == "current"
assert (failed._undo, failed._redo,
        failed._undo_names, failed._redo_names) == before
print("PASS failed Video Undo restores project and both history cursors")

saved = Probe(True)
assert saved._undo_action() is True
assert saved.state == "older"
assert saved._undo == [] and saved._redo[-1] == "current"
assert saved._redo_names[-1] == "Cut"
print("PASS durable Video Undo advances normally")

failed_redo = Probe(False)
before = (list(failed_redo._undo), list(failed_redo._redo),
          list(failed_redo._undo_names), list(failed_redo._redo_names))
assert failed_redo._redo_action() is False
assert failed_redo.state == "current"
assert (failed_redo._undo, failed_redo._redo,
        failed_redo._undo_names, failed_redo._redo_names) == before
print("PASS failed Video Redo restores project and both history cursors")
print("RESULT: PASS")
