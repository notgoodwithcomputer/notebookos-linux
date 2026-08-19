#!/usr/bin/env python3
"""Map arrow keys move the geographic centre in their named direction."""
import os
import sys
from types import SimpleNamespace

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import maps  # noqa: E402


class Canvas:
    def get_allocated_width(self): return 800
    def get_allocated_height(self): return 600
    def queue_draw(self): pass


def moved(key, shift=False):
    app = maps.Maps.__new__(maps.Maps)
    app.cx = app.cy = 0.0
    app.scale = 100.0
    app._view_gen = 0
    app._view_anim = None
    app._view_moving = False
    app._invalidate = lambda: None
    app._save_cfg = lambda: True
    state = maps.Gdk.ModifierType.SHIFT_MASK if shift else 0
    assert app._on_canvas_key(Canvas(), SimpleNamespace(keyval=key, state=state))
    return app.cx, app.cy


def main():
    assert moved(maps.Gdk.KEY_Left) == (-1.0, 0.0)
    assert moved(maps.Gdk.KEY_Right) == (1.0, 0.0)
    assert moved(maps.Gdk.KEY_Up) == (0.0, 0.75)
    assert moved(maps.Gdk.KEY_Down) == (0.0, -0.75)
    assert moved(maps.Gdk.KEY_Up, True) == (0.0, 3.0)
    print("PASS arrow keys pan west/east/north/south in map coordinates")
    print("PASS Shift preserves direction and makes a half-screen jump")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
