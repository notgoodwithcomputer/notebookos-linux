#!/usr/bin/env python3
"""Headless regression for Cookbook close-time recovery failure."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import cookbook  # noqa: E402


def bare(dirty, save_ok):
    app = cookbook.Cookbook.__new__(cookbook.Cookbook)
    app._recovery_dirty = dirty
    app.saves = 0
    def save():
        app.saves += 1
        return save_ok
    app._save_state = save
    return app


app = bare(False, False)
assert app._on_delete() is False and app.saves == 0
print("PASS clean cookbook closes without a needless failing retry")

# The veto is never silent: a refused write puts up nbapp.close_unsaved_card
# (why, and "Close Without Saving"). Stand in for the card -- a real one
# blocks on dlg.run() -- and answer it both ways.
offered = []
cookbook.nbapp.close_unsaved_card = lambda win, exc, path=None: (
    offered.append(path) or False)                 # the person keeps the window
app = bare(True, False)
assert app._on_delete() is True and app.saves == 1
assert offered == [cookbook.COOKBOOK_FILE]
print("PASS failed recovery write vetoes close with cookbook edits alive, and says why")

cookbook.nbapp.close_unsaved_card = lambda win, exc, path=None: True
app = bare(True, False)
assert app._on_delete() is False and app.saves == 1
print("PASS choosing Close Without Saving on the card really closes")
cookbook.nbapp.close_unsaved_card = lambda win, exc, path=None: False

app = bare(True, True)
assert app._on_delete() is False and app.saves == 1
print("PASS successful close-time recovery allows close")

source = open(cookbook.__file__, encoding="utf-8").read()
assert 'self.connect("delete-event", self._on_delete)' in source
print("PASS constructor wires the close veto before destruction")
print("RESULT: PASS")
