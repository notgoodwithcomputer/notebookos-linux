#!/usr/bin/env python3
"""Headless regression for New Contact during a failed edit commit."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import contacts  # noqa: E402


class Probe:
    _new_contact = contacts.Contacts._new_contact

    def __init__(self):
        self.editing = True
        self.people = [{"name": "Durable old value"}]
        self.commits = 0
        self.advanced = False

    def _commit_edits(self):
        self.commits += 1
        return False
    def _finish_new_card(self):
        self.advanced = True
        raise AssertionError("failed commit must not leave the current form")


app = Probe()
before = list(app.people)
app._new_contact()
checks = [
    (app.commits == 1, "New attempts to persist the current edit first"),
    (app.people == before and not app.advanced,
     "failed edit persistence keeps the current contact form intact"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
