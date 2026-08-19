#!/usr/bin/env python3
"""Headless regression for Calculator angle-mode persistence failures."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import calculator  # noqa: E402


def bare(save_ok):
    app = calculator.Calculator.__new__(calculator.Calculator)
    app.deg = True
    app.refreshes = 0
    app._save_prefs = lambda: save_ok
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    return app


app = bare(False)
app._set_deg(False)
assert app.deg is True and app.refreshes == 1
print("PASS failed RAD preference save restores durable DEG mode")

app = bare(True)
app._set_deg(False)
assert app.deg is False and app.refreshes == 1
print("PASS durable RAD preference save retains selected mode")

app = bare(False)
app.error = False
app._tape_i = None
app.expr = ""
app.history = ""
app.just_evaled = False
app.second = False
app.press(("DEG", "deg", None, "mode"))
assert app.deg is True
print("PASS failed keypad angle toggle also restores durable mode")
print("RESULT: PASS")
