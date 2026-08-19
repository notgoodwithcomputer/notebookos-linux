#!/usr/bin/env python3
"""Deferred Language exercise focus must obey lesson/window ownership."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/language.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(node for node in tree.body
           if isinstance(node, ast.ClassDef) and node.name == "Language")
fn = copy.deepcopy(next(node for node in cls.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_lesson_later"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
queued = []
scope = {"GLib": SimpleNamespace(
    timeout_add=lambda _ms, callback: queued.append(callback) or len(queued))}
exec(compile(module, str(SOURCE), "exec"), scope)


class Probe:
    _lesson_later = scope["_lesson_later"]
    def __init__(self):
        self._lesson_gen = 1; self._lesson_sources = set(); self._closed = False


probe = Probe(); calls = []
probe._lesson_later(0, lambda: calls.append("focus"))
probe._lesson_gen += 1
assert queued.pop(0)() is False and calls == [] and not probe._lesson_sources

probe._lesson_later(0, lambda: calls.append("focus"))
probe._closed = True
assert queued.pop(0)() is False and calls == [] and not probe._lesson_sources

source = SOURCE.read_text(encoding="utf-8")
assert "GLib.idle_add(b.grab_focus)" not in source
assert "GLib.idle_add(entry.grab_focus)" not in source
assert "self._lesson_later(0, b.grab_focus)" in source
assert "self._lesson_later(0, entry.grab_focus)" in source
print("PASS stale exercise focus callbacks are owned and suppressed")
print("RESULT: ALL PASS")
