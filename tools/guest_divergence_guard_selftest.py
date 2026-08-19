#!/usr/bin/env python3
"""Display-free scope checks for binary exception-guard classification."""
import ast
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))
import guest_divergence_check as gate  # noqa: E402


def call(source):
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "run")
    return id(node) in gate._try_guarded_nodes(tree)


assert not call("""
try:
    def later():
        subprocess.run(['missing'])
except OSError:
    pass
later()
""")
print("PASS a nested function is not protected by its definition-time try")

assert not call("""
try:
    subprocess.run(['missing'])
except KeyError:
    pass
""")
print("PASS an unrelated exception handler is not an executable guard")

assert call("""
try:
    subprocess.run(['missing'])
except OSError:
    pass
""")
print("PASS a direct call caught by OSError remains guarded")

print("RESULT: ALL PASS")
