#!/usr/bin/env python3
"""Headless regression for edits over a protected Screenplay recovery store."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import screenplay  # noqa: E402


class Probe:
    _on_delete = screenplay.Screenplay._on_delete

    def __init__(self, accept):
        self._recovery_dirty = True
        self._recovery_store_writable = False
        self._save_error = None
        self.accept = accept
        self.asked = []

    def _save_doc(self):
        return False

    def _confirm(self, title, body, action):
        self.asked.append((title, body, action))
        return self.accept


keep = Probe(False)
discard = Probe(True)
checks = [
    (keep._on_delete() is True and keep.asked
     and keep.asked[0][2] == "Close Without Saving",
     "protected recovery edits veto close unless loss is confirmed"),
    (discard._on_delete() is False and len(discard.asked) == 1,
     "explicit Close Without Saving permits the requested close"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
