#!/usr/bin/env python3
"""The notification bell speaks its current unread count."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="shell-bell-"))
import shell  # noqa: E402


class Accessible:
    def __init__(self): self.name = ""
    def set_name(self, name): self.name = name


class Bell:
    def __init__(self): self.tooltip, self.acc = "", Accessible()
    def set_tooltip_text(self, text): self.tooltip = text
    def get_accessible(self): return self.acc


class Image:
    def set_from_surface(self, _surface): pass


app = shell.Panel.__new__(shell.Panel)
app.bell, app.bellimg = Bell(), Image()
app._bell_unread, app._bell_count = False, -1
for count, expected in ((0, "Notifications"), (1, "1 new notification"),
                        (2, "2 new notifications"), (0, "Notifications")):
    app._paint_bell(count)
    assert app.bell.tooltip == expected, (count, app.bell.tooltip)
    assert app.bell.acc.name == expected, (count, app.bell.acc.name)
print("PASS notification tooltip and accessible name track every unread count")
print("RESULT: PASS")
