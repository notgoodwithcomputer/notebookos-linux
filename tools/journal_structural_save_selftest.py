#!/usr/bin/env python3
"""Source/behavior regression for Journal structural save truthfulness."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
import journal  # noqa: E402


class Chip:
    def __init__(self): self.value = ""
    def set_markup(self, value): self.value = value


class Probe:
    _mark_unsaved = journal.Journal._mark_unsaved


app = Probe()
app.save = Chip()
app._mark_unsaved()
behavior = "Not saved" in app.save.value and "#C8341E" in app.save.value

source = open(os.path.join(DE, "journal.py"), encoding="utf-8").read()
new = source[source.index("    def new_entry("):
             source.index("    def select_entry(")]
delete = source[source.index("    def _remove_active("):
                source.index("    def _confirm(", source.index("    def _remove_active("))]
checks = [
    (behavior, "failed structural writes expose Not saved state"),
    ("if self._persist():" in new and "self._mark_unsaved()" in new,
     "new entry gates Saved state on persistence"),
    ("if not self._persist():" in delete
     and "self.entries = before_entries" in delete,
     "failed delete restores the entry model"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
# Terminal verdict: the release runner will not read success into a zero exit
# with only PASS lines -- a suite that dies half way prints those too.
_ok = all(ok for ok, _name in checks)
print("RESULT: %s" % ("ALL PASS" if _ok else "FAILED"))
raise SystemExit(not _ok)
