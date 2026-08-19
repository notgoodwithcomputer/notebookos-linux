#!/usr/bin/env python3
"""Display-free checks that failed Bills writes never become success state."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import bills  # noqa: E402


class Probe:
    _bill = bills.Bills._bill
    _do_delete = bills.Bills._do_delete
    _undo_delete = bills.Bills._undo_delete
    def __init__(self):
        self.bills = [{"id": "b1", "payee": "Rent", "paid": []}]
        self.sel = "b1"; self._deleted = None; self.flashes = []; self.refreshes = 0
    def _close_menu(self): pass
    def _close_overlay(self): pass
    def _save(self): return False
    def _refresh(self): self.refreshes += 1
    def _flash(self, text): self.flashes.append(text)


app = Probe(); original = app.bills[0]
app._do_delete("b1")
delete_ok = (app.bills == [original] and app.sel == "b1"
             and app._deleted is None and not app.flashes)

other = {"id": "b2", "payee": "Power", "paid": []}
app._deleted = (0, original); app.bills = [other]; app.sel = "b2"
app._undo_delete()
undo_ok = (app.bills == [other] and app._deleted == (0, original)
           and app.sel == "b2" and app.refreshes == 1 and not app.flashes)

for ok, name in ((delete_ok, "failed delete rolls back without success"),
                 (undo_ok, "failed restore rolls back without success")):
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if delete_ok and undo_ok else "FAILED"))
raise SystemExit(not (delete_ok and undo_ok))
