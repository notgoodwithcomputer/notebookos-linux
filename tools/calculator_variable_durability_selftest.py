#!/usr/bin/env python3
"""Headless regression for Calculator memory-register persistence."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import calculator  # noqa: E402


class Probe:
    _store_variable = calculator.Calculator._store_variable

    def __init__(self, variables, save_ok):
        self.variables = dict(variables)
        self.save_ok = save_ok
    def _save_prefs(self): return self.save_ok


new_failed = Probe({}, False)
replace_failed = Probe({"A": 4.0}, False)
passed = Probe({"A": 4.0}, True)
checks = [
    (new_failed._store_variable("B", 7.0) is False
     and "B" not in new_failed.variables,
     "failed new register write does not create volatile memory"),
    (replace_failed._store_variable("A", 9.0) is False
     and replace_failed.variables["A"] == 4.0,
     "failed register replacement restores its durable value"),
    (passed._store_variable("A", 9.0) is True
     and passed.variables["A"] == 9.0,
     "successful register write remains available"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
