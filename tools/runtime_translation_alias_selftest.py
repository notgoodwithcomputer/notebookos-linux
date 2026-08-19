#!/usr/bin/env python3
"""A formatted display cannot evade the runtime gate through a local alias."""
import importlib.util
import sys
import tempfile
from pathlib import Path

p = Path(__file__).with_name("runtime_translation_check.py")
s = importlib.util.spec_from_file_location("runtime_translation", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
    f.write('def update(label, name):\n text = "of %s" % name\n label.set_text(text)\n')
    path = f.name
assert m.scan(path, {"of %s"}) == [(3, "update", "of %s")]
with open(path, "w") as f:
    f.write('def update(label, name):\n text = _t("of %s") % name\n label.set_text(text)\n')
assert m.scan(path, {"of %s"}) == []
print("PASS runtime translation follows a straight-line local alias")
print("RESULT: PASS")
