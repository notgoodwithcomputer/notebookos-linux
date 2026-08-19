#!/usr/bin/env python3
"""Dynamic Video transport exposes the action it currently performs."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="video-a11y-"))
import video  # noqa: E402


class Accessible:
    def __init__(self): self.name = ""
    def set_name(self, name): self.name = name


class Button:
    def __init__(self): self.tooltip, self.acc = "", Accessible()
    def set_tooltip_text(self, text): self.tooltip = text
    def get_accessible(self): return self.acc


app = video.VideoEditor.__new__(video.VideoEditor)
app._play_img = None
app._play_w = Button()
app._set_play_glyph(False)
assert app._play_w.tooltip == "Play" and app._play_w.acc.name == "Play"
app._set_play_glyph(True)
assert app._play_w.tooltip == "Stop" and app._play_w.acc.name == "Stop"
app._set_play_glyph(False)
assert app._play_w.tooltip == "Play" and app._play_w.acc.name == "Play"
print("PASS Video transport accessible name follows Play and Stop")
print("RESULT: PASS")
