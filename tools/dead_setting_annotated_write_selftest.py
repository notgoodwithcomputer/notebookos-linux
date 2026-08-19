#!/usr/bin/env python3
"""Annotated mapping writes count as persisted setting writes."""
import ast
import importlib.util
import sys
from pathlib import Path

p = Path(__file__).with_name("dead_setting_check.py")
s = importlib.util.spec_from_file_location("dead_setting", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)

def results(write):
    src = '''\nclass App:\n def build(self):\n  self.fullscreen.set_active(self.settings.get("fullscreen", False))\n  self.fullscreen.connect("toggled", self.changed)\n def changed(self, w):\n  %s\n''' % write
    scan = m.Scan("demo.py", ast.parse(src)).run()
    return m.verdicts(scan)

plain = results('self.settings["fullscreen"] = w.get_active()')
annotated = results('self.settings["fullscreen"]: bool = w.get_active()')
assert plain and annotated and plain[0][0:2] == annotated[0][0:2]
assert annotated[0][1] == "ROUND TRIP"
print("PASS annotated and plain setting writes receive the same verdict")
print("RESULT: PASS")
