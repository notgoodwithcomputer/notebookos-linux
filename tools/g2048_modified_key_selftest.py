#!/usr/bin/env python3
"""Desktop modifier chords must not mutate the 2048 board."""
import sys
from pathlib import Path
from types import SimpleNamespace
DE = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
      "rootfs-overlay/opt/notebook/de")
sys.path.insert(0, str(DE))
import g2048
game = g2048.Game2048.__new__(g2048.Game2048)
game._menu_open = None; game._about_layer = None; moves = []
game.move = moves.append
plain = SimpleNamespace(keyval=g2048.Gdk.KEY_Left, state=0)
assert game._on_game_key(None, plain) and moves == ["left"]
for mask in (g2048.Gdk.ModifierType.CONTROL_MASK,
             g2048.Gdk.ModifierType.MOD1_MASK,
             g2048.Gdk.ModifierType.SUPER_MASK):
    ev = SimpleNamespace(keyval=g2048.Gdk.KEY_Left, state=mask)
    assert not game._on_game_key(None, ev)
assert moves == ["left"]
print("PASS modified navigation chords do not move the 2048 board")
print("RESULT: PASS")
