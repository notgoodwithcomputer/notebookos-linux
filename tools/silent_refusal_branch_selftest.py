#!/usr/bin/env python3
"""One-sided feedback must not launder a later silent refusal."""
import ast
import importlib.util
import sys
from pathlib import Path

p = Path(__file__).with_name("silent_refusal_check.py")
s = importlib.util.spec_from_file_location("silent", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
src = '''\nclass App:\n def run(self):\n  if self.ready:\n   self._flash("Ready")\n  if not self.allowed:\n   return\n'''
tree = ast.parse(src); method = tree.body[0].body[0]; out = []
m._scan_block(method.body, [], False, src, out)
assert len(out) == 1 and "allowed" in out[0][2]
print("PASS branch-only feedback does not hide a later silent refusal")
print("RESULT: PASS")
