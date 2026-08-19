#!/usr/bin/env python3
"""An unrelated have() call cannot launder a missing binary invocation."""
import ast
import importlib.util
import sys
from pathlib import Path
p = Path(__file__).with_name("guest_divergence_check.py")
s = importlib.util.spec_from_file_location("guest_div", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)

def guarded(src):
    tree = ast.parse(src)
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "run")
    return m._probe_guarded(call, "missing-tool", parents)

assert not guarded('def diagnostic(): return _have("missing-tool")\n'
                   'def launch(): subprocess.run(["missing-tool"])\n')
assert guarded('def launch():\n if _have("missing-tool"):\n'
               '  subprocess.run(["missing-tool"])\n')
assert guarded('def launch():\n if not _have("missing-tool"): return\n'
               ' subprocess.run(["missing-tool"])\n')
print("PASS only a dominating probe guards a binary invocation")
print("RESULT: PASS")
