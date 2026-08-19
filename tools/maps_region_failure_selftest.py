#!/usr/bin/env python3
"""A failed region choice cannot disagree with the visible map."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="maps-region-"))
import maps  # noqa: E402


class Combo:
    def __init__(self, active): self.active = active
    def get_active_id(self): return self.active
    def set_active_id(self, value): self.active = value
    def set_active(self, _index): self.active = None


class Pack:
    def __init__(self, path): self.path = path


class Stand:
    _on_region_changed = maps.Maps._on_region_changed
    def __init__(self, old, succeeds):
        self.pack = Pack(old) if old else None
        self.succeeds = succeeds
        self.saved = 0
    def _open_map(self, _path): return self.succeeds
    def _save_cfg(self): self.saved += 1


combo = Combo("damaged.nbm2")
app = Stand("good.nbm2", False)
app._on_region_changed(combo)
assert combo.active == "good.nbm2" and app.pack.path == "good.nbm2"

combo = Combo("good.nbm2")
empty = Stand(None, False)
empty._on_region_changed(combo)
assert combo.active is None

combo = Combo("next.nbm2")
ok = Stand("good.nbm2", True)
ok._on_region_changed(combo)
assert combo.active == "next.nbm2" and ok.saved == 1
print("PASS failed region selection follows the map that remains visible")
print("RESULT: PASS")
