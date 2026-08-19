#!/usr/bin/env python3
"""Headless regression for failed Accounting deletions."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import accounting  # noqa: E402


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_result):
    app = accounting.Accounting.__new__(accounting.Accounting)
    app.tx = [{"desc": "Rent", "amt": -1200.0},
              {"desc": "Pay", "amt": 2500.0}]
    app.undo = UndoProbe()
    app._autosave = lambda: save_result
    app.events = []
    app._close_confirm = lambda: app.events.append("confirm closed")
    app._close_edit = lambda: app.events.append("editor closed")
    app._refresh = lambda: app.events.append("refreshed")
    app._flash = lambda message: app.events.append(message)
    return app


app = bare(False)
original = list(app.tx)
app._delete_tx(0)
assert app.tx == original, app.tx
assert app.undo.calls == [("checkpoint", "Delete Entry")], app.undo.calls
assert app.events == [], app.events
print("PASS failed ledger deletion restores the exact row without success UI")

app = bare(True)
app._delete_tx(0)
assert [row["desc"] for row in app.tx] == ["Pay"], app.tx
assert app.undo.calls == [("checkpoint", "Delete Entry"), ("commit", None)]
assert app.events[:3] == ["confirm closed", "editor closed", "refreshed"]
print("PASS durable ledger deletion commits and closes normally")
print("RESULT: PASS")
