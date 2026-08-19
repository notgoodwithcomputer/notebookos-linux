#!/usr/bin/env python3
"""Display-free contract for the desktop board's startup map watchdog."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import widgets  # noqa: E402


class Window:
    def is_viewable(self):
        return False


board = widgets.Widgets.__new__(widgets.Widgets)
calls = []
board.get_window = lambda: Window()
board.hide = lambda: calls.append("hide")
board.show_all = lambda: calls.append("show")
board._stay_down = lambda: calls.append("lower")

board._app_active = lambda: True
assert board._ensure_mapped() is False and calls == []
print("PASS intentionally hidden board is not remapped over an active app")

board._app_active = lambda: False
assert board._ensure_mapped() is False
assert calls == ["hide", "show", "lower"]
print("PASS inactive, non-viewable board is still repaired")

print("RESULT: ALL PASS")
