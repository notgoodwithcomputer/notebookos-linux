#!/usr/bin/env python3
"""Nested self-attribute targets do not manufacture the base attribute."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "self_attr_audit.py")
spec = importlib.util.spec_from_file_location("self_attr_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

source = """
class Mutant:
    def broken(self):
        self._ghost.value = 1
        self._phantom[0] = 2
        self._ghost()

    def valid(self):
        self._left, *self._rest = (1, 2, 3)
        return self._left, self._rest

    def also_broken(self):
        self._counter += 1
        self._declared: int
        return self._declared
"""
with tempfile.TemporaryDirectory(prefix="self-attr-target-") as td:
    path = os.path.join(td, "mutant.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    classes, imports, _methods = audit.collect([path])
    info = classes[("mutant", "Mutant")]
    defines, _names, _values, unresolved = audit.chain(info, classes, imports)

assert unresolved is None
assert "_ghost" not in defines
assert "_phantom" not in defines
assert "_left" in defines
assert "_rest" in defines
assert "_counter" not in defines
assert "_declared" not in defines
uses = {name for name, _line, _call in info.uses}
assert {"_ghost", "_phantom", "_left", "_rest"} <= uses
assert {"_counter", "_declared"} <= uses

print("SELF ATTR NESTED TARGET SELFTEST: 9 checks, all pass")
print("RESULT: ALL PASS")
