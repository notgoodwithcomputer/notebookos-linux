#!/usr/bin/env python3
"""At-risk signed labels remain visible to the RTL gate through aliases."""
import ast
import importlib.util
import sys
from pathlib import Path
p = Path(__file__).with_name("rtl_check.py")
s = importlib.util.spec_from_file_location("rtl", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
direct = ast.parse('def f(label,n): label.set_text("+%d" % n)')
alias = ast.parse('def f(label,n):\n text = "+%d" % n\n label.set_text(text)')
wrapped = ast.parse('def f(label,n):\n text = ltr("+%d" % n)\n label.set_text(text)')
assert m._visible_at_risk(direct) and m._visible_at_risk(alias)
risky = m._visible_at_risk(wrapped); handled = m._ltr_wrapped_literals(wrapped)
assert risky and all(nid in handled for _ln, _text, nid in risky)
print("PASS RTL risk and ltr protection both survive a local alias")
print("RESULT: PASS")
