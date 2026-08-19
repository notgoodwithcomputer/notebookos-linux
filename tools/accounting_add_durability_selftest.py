#!/usr/bin/env python3
"""Headless regression for failed Accounting entry additions."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import accounting  # noqa: E402


class Entry:
    def __init__(self, text):
        self.text = text
        self.focused = False

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text

    def grab_focus(self):
        self.focused = True


class Undo:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_ok):
    app = accounting.Accounting.__new__(accounting.Accounting)
    app.tx = []
    app.f_desc = Entry("Rent")
    app.f_amt = Entry("1250.00")
    app.fdate = Entry("15 Aug")
    app.fdir = "debit"
    app._form_date = "15 Aug"
    app._form_iso = "2026-08-15"
    app._form_error = lambda _message: None
    app.undo = Undo()
    app._autosave = lambda: save_ok
    app._terms = []
    app._shown = 0
    app._append_one_row = lambda: True
    app._flash = lambda _message: None
    return app


app = bare(False)
app._on_add()
assert app.tx == []
assert app.f_desc.text == "Rent" and app.f_amt.text == "1250.00"
assert app.f_amt.focused and app.undo.calls[-1] == ("commit", None)
print("PASS failed ledger add restores model and preserves typed form")

app = bare(True)
app._on_add()
assert len(app.tx) == 1 and app.tx[0]["desc"] == "Rent"
assert app.f_desc.text == "" and app.f_amt.text == ""
print("PASS durable ledger add commits and clears form")
print("RESULT: PASS")
