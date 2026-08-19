#!/usr/bin/env python3
"""Default Applications rolls its picker back when persistence fails."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="settings-default-"))
import settings  # noqa: E402


class Combo:
    def __init__(self, active): self.active = active
    def get_active(self): return self.active
    def set_active(self, active): self.active = active


label, exts, default = settings.DEFAULT_APP_CATEGORIES[0]
mods = [module for module, _display in settings.APP_CHOICES]
old = default
new = next(module for module in mods if module != old)
app = settings.Settings.__new__(settings.Settings)
app._settings = {"default_apps": {ext: old for ext in exts}, "unrelated": 9}
app._da_changing = False
app._da_status = object()
statuses = []
app._set_status = lambda _lbl, text, warn=False: statuses.append((text, warn))
app._save_settings = lambda: False
combo = Combo(mods.index(new))
app._on_defaultapp(combo, exts)
assert all(app._settings["default_apps"][ext] == old for ext in exts)
assert combo.active == mods.index(old)
assert statuses and statuses[-1][1] is True

app._save_settings = lambda: True
combo.active = mods.index(new)
app._on_defaultapp(combo, exts)
assert all(app._settings["default_apps"][ext] == new for ext in exts)
assert app._settings["unrelated"] == 9
print("PASS failed default-app save restores mapping and picker")
print("PASS successful category change updates every extension")
print("RESULT: PASS")
