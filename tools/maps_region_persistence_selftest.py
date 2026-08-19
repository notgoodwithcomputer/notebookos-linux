#!/usr/bin/env python3
"""Headless regression for persisting an explicitly selected map region."""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import maps  # noqa: E402


class Combo:
    def __init__(self, path):
        self.path = path
        self.active_id_calls = []
        self.active_calls = []

    def get_active_id(self):
        return self.path

    def set_active_id(self, path):
        self.path = path
        self.active_id_calls.append(path)

    def set_active(self, index):
        self.path = None if index == -1 else self.path
        self.active_calls.append(index)


class Probe:
    _on_region_changed = maps.Maps._on_region_changed

    def __init__(self, opens, old_path=None):
        self.opens = opens
        self.opened = []
        self.saves = 0
        self.pack = SimpleNamespace(path=old_path) if old_path else None
        self._changing_region = False

    def _open_map(self, path):
        self.opened.append(path)
        return self.opens

    def _save_cfg(self):
        self.saves += 1


success = Probe(True)
success._on_region_changed(Combo("/maps/europe.nbm2"))
damaged_combo = Combo("/maps/damaged.nbm2")
damaged = Probe(False, "/maps/europe.nbm2")
damaged._on_region_changed(damaged_combo)
first_combo = Combo("/maps/damaged.nbm2")
first_damaged = Probe(False)
first_damaged._on_region_changed(first_combo)
empty = Probe(True)
empty._on_region_changed(Combo(None))

checks = [
    (success.opened == ["/maps/europe.nbm2"] and success.saves == 1,
     "successful region selection is persisted immediately"),
    (damaged.opened == ["/maps/damaged.nbm2"] and damaged.saves == 0
     and damaged_combo.path == "/maps/europe.nbm2"
     and damaged_combo.active_id_calls == ["/maps/europe.nbm2"]
     and not damaged_combo.active_calls and not damaged._changing_region,
     "failed region open restores the selector to the remembered pack"),
    (first_damaged.opened == ["/maps/damaged.nbm2"]
     and first_damaged.saves == 0 and first_combo.path is None
     and first_combo.active_calls == [-1]
     and not first_combo.active_id_calls
     and not first_damaged._changing_region,
     "failed first region open clears the selector when no pack is active"),
    (not empty.opened and empty.saves == 0,
     "empty combo selection neither opens nor saves"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(bool(ok) for ok, _name in checks)
print("RESULT: %d checks, ALL PASS (%d/%d)"
      % (len(checks), passed, len(checks)))
raise SystemExit(passed != len(checks))
