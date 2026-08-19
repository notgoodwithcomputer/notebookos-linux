#!/usr/bin/env python3
"""Journal must veto close while its only live edit cannot be persisted."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/journal.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Journal")
fn = copy.deepcopy(next(n for n in cls.body
                        if isinstance(n, ast.FunctionDef) and n.name == "_on_delete"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
# _on_delete puts up nbapp.close_unsaved_card on a refused write (it says
# why and offers "Close Without Saving"); the extracted function needs an
# nbapp in scope, and the stand-in lets the check answer the card both ways.
class _NbappStandIn:
    answer = False                       # the person keeps the window
    offered = []

    @classmethod
    def close_unsaved_card(cls, win, exc, path=None):
        cls.offered.append((exc, path))
        return cls.answer

scope = {"nbapp": _NbappStandIn, "JOURNAL_FILE": "journal.json",
         "getattr": getattr}
exec(compile(module, str(SOURCE), "exec"), scope)


class Probe:
    _on_delete = scope["_on_delete"]

    def __init__(self, dirty, save_ok):
        self._recovery_dirty = dirty
        self.save_ok = save_ok
        self.folded = 0
        self.writes = 0

    def _save_current(self): self.folded += 1
    def _persist(self):
        self.writes += 1
        if self.save_ok:
            self._recovery_dirty = False
        return self.save_ok


failed = Probe(True, False)
assert failed._on_delete() is True
assert (failed.folded, failed.writes, failed._recovery_dirty) == (1, 1, True)
assert _NbappStandIn.offered, "the veto must offer the close card"
print("PASS failed Journal recovery write vetoes close with the live edit intact, and says why")

_NbappStandIn.answer = True              # they chose Close Without Saving
assert Probe(True, False)._on_delete() is False
_NbappStandIn.answer = False
print("PASS choosing Close Without Saving on the card really closes")

saved = Probe(True, True)
assert saved._on_delete() is False
assert (saved.folded, saved.writes, saved._recovery_dirty) == (1, 1, False)
print("PASS durable Journal recovery write allows close")

clean = Probe(False, False)
assert clean._on_delete() is False
assert (clean.folded, clean.writes) == (0, 0)
print("PASS clean Journal close performs no redundant write")

source = SOURCE.read_text(encoding="utf-8")
assert 'self.connect("delete-event", self._on_delete)' in source
print("PASS constructor wires the recovery veto before destroy")
# The release runner will not read success into a zero exit with no terminal
# verdict -- a suite that dies half way also prints PASS lines. Say it.
print("RESULT: ALL PASS")
