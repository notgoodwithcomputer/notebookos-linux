#!/usr/bin/env python3
"""Headless regression for Calculator display precision persistence."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import calculator  # noqa: E402


class Probe:
    _set_fix = calculator.Calculator._set_fix
    def __init__(self, fix, save_ok):
        self.fix = fix; self.save_ok = save_ok; self.refreshes = 0
    def _save_prefs(self): return self.save_ok
    def _refresh(self): self.refreshes += 1


failed = Probe(None, False)
passed = Probe(None, True)
checks = [
    (failed._set_fix(2) is False and failed.fix is None
     and failed.refreshes == 1,
     "failed display-mode write restores Float rendering"),
    (passed._set_fix(2) is True and passed.fix == 2
     and passed.refreshes == 1,
     "successful display-mode write applies Fix 2 rendering"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
