#!/usr/bin/env python3
"""Render the GBA IDE editor panes (populated with the example game) so the
'unintuitive/ugly' surfaces can be judged and fixed at source."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import uishot
uishot.load_theme()
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

DE = os.path.join(os.path.dirname(__file__), "..",
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.abspath(DE))
os.environ.setdefault("NB_HOME", "/tmp/claude-1000/-home-ben-Documents-notebookos-linux/"
                      "5f4eb55f-183f-416a-a12a-792db7e89cb4/scratchpad/nbhome")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)
import gbaide

SP = sys.argv[1] if len(sys.argv) > 1 else "/tmp"

def render(name, sel):
    app = gbaide.GbaIde()
    def after(win):
        win._file_example()
        if sel:
            win._select_resource(*sel)
    uishot.shot_window(app, 1240, 780, os.path.join(SP, "gba_%s.png" % name),
                       settle=120, after_show=after)
    print("wrote gba_%s.png" % name)
    app.destroy()

render("welcome", None)
render("sprite", ("sprite", 0))
render("object", ("object", 0))
render("room", ("room", 0))
render("sound", ("sound", 0))
render("tileset", ("tileset", 0))
