#!/usr/bin/env python3
"""Headless regression checks for Bills sort persistence failures."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import bills  # noqa: E402


def bare(save_result):
    app = bills.Bills.__new__(bills.Bills)
    app.sort = "due"
    app._save = lambda: save_result
    app.refreshes = 0
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    return app


app = bare(False)
app._set_sort("payee")
assert app.sort == "due", app.sort
assert app.refreshes == 1, app.refreshes
print("PASS failed sort save restores the durable ordering")

app = bare(True)
app._set_sort("payee")
assert app.sort == "payee", app.sort
assert app.refreshes == 1, app.refreshes
print("PASS successful sort save keeps the new ordering")

app = bare(True)
app._set_sort("due")
assert app.refreshes == 0, app.refreshes
print("PASS selecting the active ordering performs no write or refresh")
print("RESULT: PASS")
