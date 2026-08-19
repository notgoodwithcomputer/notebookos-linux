#!/usr/bin/env python3
"""Display-free regression for Edit Entry persistence failure."""
import copy
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="account-edit-"))
import accounting  # noqa: E402


class Entry:
    def __init__(self, text): self.text, self.focused = text, False
    def get_text(self): return self.text
    def grab_focus(self): self.focused = True


class Undo:
    def __init__(self): self.events = []
    def checkpoint(self, label): self.events.append(("checkpoint", label))
    def commit(self): self.events.append(("commit", None))


old = {"date": "15 Aug", "iso": "2026-08-15",
       "desc": "Old description", "amt": 12.0}
app = accounting.Accounting.__new__(accounting.Accounting)
app.tx = [copy.deepcopy(old)]
app._edit_idx = 0
app._e_amt = Entry("27.50")
app._e_desc = Entry("Attempted edit")
app._e_date = Entry("15 Aug")
app._edir = "credit"
app.undo = Undo()
app._autosave = lambda: False
app._close_edit = lambda: (_ for _ in ()).throw(AssertionError("editor closed"))
app._refresh = lambda: (_ for _ in ()).throw(AssertionError("row refreshed"))
app._flash = lambda _msg: (_ for _ in ()).throw(AssertionError("success flashed"))

app._save_edit()
assert app.tx == [old], app.tx
assert app._e_desc.get_text() == "Attempted edit" and app._e_desc.focused
assert app.undo.events == [("checkpoint", "Edit Entry"), ("commit", None)]
print("PASS failed Accounting edit restores the model and leaves fields open")

opening = accounting.Accounting.__new__(accounting.Accounting)
opening.opening = 100.0
opening._o_amt = Entry("250")
opening._odir = "credit"
opening.undo = Undo()
opening._autosave = lambda: False
opening._close_edit = lambda: (_ for _ in ()).throw(AssertionError("editor closed"))
opening._refresh = lambda: (_ for _ in ()).throw(AssertionError("view refreshed"))
opening._flash = lambda _msg: (_ for _ in ()).throw(AssertionError("success flashed"))
opening._save_opening()
assert opening.opening == 100.0 and opening._o_amt.focused
assert opening.undo.events == [("checkpoint", "Opening Balance"),
                               ("commit", None)]
print("PASS failed opening-balance save restores the model and editor")
print("RESULT: PASS")
