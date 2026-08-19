#!/usr/bin/env python3
"""Screen blanking must roll runtime and UI back when persistence fails."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/settings.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Settings")
fn = copy.deepcopy(next(n for n in cls.body
                        if isinstance(n, ast.FunctionDef) and n.name == "_on_blank"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"nbapp": type("NB", (), {"note_save_failure": staticmethod(lambda *a: None)}),
         "CFG_FILE": "/settings.json"}
exec(compile(module, str(SOURCE), "exec"), scope)


class Combo:
    def __init__(self, active): self.active = active; self.changes = []
    def get_active(self): return self.active
    def set_active(self, value): self.active = value; self.changes.append(value)


class Probe:
    _on_blank = scope["_on_blank"]

    def __init__(self, save_ok):
        self._suppress_blank = False
        self._blank_opts = [("Never", 0), ("5 minutes", 300)]
        self._settings = {"blank_timeout": 0}
        self.save_ok = save_ok
        self.applied = []

    def _cfg_int(self, key, default): return int(self._settings.get(key, default))
    def _apply_blank(self, secs): self.applied.append(secs); return True
    def _save_settings(self): return self.save_ok


failed = Probe(False); failed_combo = Combo(1)
assert failed._on_blank(failed_combo) is False
assert failed._settings["blank_timeout"] == 0
assert failed.applied == [300, 0] and failed_combo.active == 0
assert failed._suppress_blank is False
print("PASS failed blank-timeout save restores runtime, model, and selector")

saved = Probe(True); saved_combo = Combo(1)
assert saved._on_blank(saved_combo) is True
assert saved._settings["blank_timeout"] == 300
assert saved.applied == [300] and saved_combo.changes == []
print("PASS durable blank-timeout change remains applied")
# Terminal verdict the release runner recognises (SUCCESSWORD).
print("RESULT: ALL PASS")
