#!/usr/bin/env python3
"""Battery status detail refreshes independently of percentage ink."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="shell-battery-"))
import shell  # noqa: E402


class Label:
    def __init__(self): self.markups, self.tips, self.visible = [], [], None
    def set_markup(self, value): self.markups.append(value)
    def set_tooltip_text(self, value): self.tips.append(value)
    def set_visible(self, value): self.visible = value


app = shell.Panel.__new__(shell.Panel)
app.batlbl = Label()
app._last_bat = app._last_bat_tip = None
app._paint_battery("80%", "Battery 80% · Not charging")
app._paint_battery("80%", "Battery 80% · Discharging")
assert len(app.batlbl.markups) == 1
assert app.batlbl.tips == ["Battery 80% · Not charging",
                           "Battery 80% · Discharging"]
app._paint_battery(None, None)
assert app.batlbl.visible is False and app.batlbl.tips[-1] == ""
print("PASS battery tooltip follows status changes at a fixed percentage")
print("RESULT: PASS")
