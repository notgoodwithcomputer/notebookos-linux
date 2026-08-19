#!/usr/bin/env python3
"""Headless regression for failed blank New Contact cleanup."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import contacts  # noqa: E402


class Probe:
    _finish_new_card = contacts.Contacts._finish_new_card
    _is_blank = contacts.Contacts._is_blank
    def __init__(self, save_ok):
        person = {k: "" for k in contacts.FIELD_KEYS}
        person.update({"phones": [], "emails": []})
        self.people = [person]
        self.active = 0
        self._pending_new = True
        self.save_ok = save_ok
    def _save(self): return self.save_ok


failed = Probe(False)
passed = Probe(True)
checks = [
    (failed._finish_new_card() is None and len(failed.people) == 1
     and failed.active == 0 and failed._pending_new,
     "failed blank-card removal restores card, selection, and retry ownership"),
    (passed._finish_new_card() == 0 and passed.people == []
     and not passed._pending_new,
     "durable blank-card removal clears the placeholder normally"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
