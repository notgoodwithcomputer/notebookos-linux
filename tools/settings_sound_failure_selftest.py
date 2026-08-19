#!/usr/bin/env python3
"""Mixer failure rejects visual/settings state instead of claiming success."""
import os
import sys
from pathlib import Path
DE = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
      "rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.fspath(DE))
import settings

class Scale:
    def __init__(self): self.value = 70
    def get_value(self): return self.value
    def set_value(self, value): self.value = value

app = settings.Settings.__new__(settings.Settings)
app._settings = {"sound.muted": False, "sound.volume": 55}
app._playback_ctl = "Speaker"; app._sound_syncing = False
app._sound_error_label = None
app._get_volume = lambda _ctl: 55
app._save_settings = lambda: True
old_run = settings.run
settings.run = lambda _cmd: (1, "failed")
try:
    assert app._on_mute(None, True) is True
    assert app._settings["sound.muted"] is False and app._sound_action_error
    scale = Scale(); app._on_vol(scale)
    assert scale.value == 55 and app._settings["sound.volume"] == 55
finally:
    settings.run = old_run
print("PASS mixer failure rejects mute and restores the volume control")
print("RESULT: PASS")
