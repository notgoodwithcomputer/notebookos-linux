#!/usr/bin/env python3
"""Sequencer's SELECT/CUT segment: real toggles, pressed, answering once.

The pair is Gtk.ToggleButtons so assistive technology can read which tool is
chosen. Because set_active emits "clicked", restating the pair from inside its
own handler once re-entered _set_tool for every button; the app now goes
through _seg_choose, which blocks each button's handler while the row is lit.
An earlier version of this check drove a hand-rolled Button fake that had no
get_active at all, so it could not see either the bug or the fix. This one
wires REAL toggle buttons exactly as the toolbar does and presses them.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
sys.setrecursionlimit(200)          # a ping-pong fails fast, not by hanging

import gi                                            # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                        # noqa: E402
import sequencer                                     # noqa: E402


class Probe:
    _set_tool = sequencer.Sequencer._set_tool

    def __init__(self):
        self.calls = []
        self.tool_btns = {}
        self.lanes = []
        # wired exactly as Sequencer's toolbar wires them
        for key in (sequencer.TOOL_SELECT, sequencer.TOOL_CUT):
            b = Gtk.ToggleButton(label=key)
            b._seg_hid = b.connect(
                "clicked", lambda _b, k=key: (self.calls.append(k),
                                              self._set_tool(k)))
            self.tool_btns[key] = b


app = Probe()
app._set_tool(sequencer.TOOL_SELECT)
select = (app.tool == sequencer.TOOL_SELECT
          and app.tool_btns[sequencer.TOOL_SELECT].get_active()
          and not app.tool_btns[sequencer.TOOL_CUT].get_active()
          and app.calls == [])
try:
    app.tool_btns[sequencer.TOOL_CUT].clicked()          # a person presses CUT
    cut = (app.tool == sequencer.TOOL_CUT
           and app.tool_btns[sequencer.TOOL_CUT].get_active()
           and not app.tool_btns[sequencer.TOOL_SELECT].get_active()
           and app.calls == [sequencer.TOOL_CUT])
    app.tool_btns[sequencer.TOOL_CUT].clicked()          # pressing the lit one
    relit = (app.tool == sequencer.TOOL_CUT
             and app.tool_btns[sequencer.TOOL_CUT].get_active()
             and app.calls == [sequencer.TOOL_CUT, sequencer.TOOL_CUT])
except RecursionError:
    cut = relit = False
source = open(os.path.join(DE, "sequencer.py"), encoding="utf-8").read()
native = 'b = Gtk.ToggleButton(label=_t(label))' in source

results = ((native, "timeline tools use semantic toggles"),
           (select, "Select exposes the initial checked state"),
           (cut, "pressing Cut transfers the checked state, once"),
           (relit, "pressing the lit tool keeps it lit, once"))
for ok, name in results:
    print(("PASS " if ok else "FAIL ") + name)
ok_all = all(ok for ok, _ in results)
print("RESULT: %s" % ("PASS" if ok_all else "FAILED"))
raise SystemExit(0 if ok_all else 1)
