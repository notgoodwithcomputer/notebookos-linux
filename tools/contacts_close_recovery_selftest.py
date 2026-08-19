#!/usr/bin/env python3
"""Headless regression for Contacts close-time edit persistence."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import contacts  # noqa: E402


def bare(editing, save_ok):
    app = contacts.Contacts.__new__(contacts.Contacts)
    app.editing = editing
    app.commits = 0
    def commit():
        app.commits += 1
        return save_ok
    app._commit_edits = commit
    return app


# The veto is never silent: a refused write puts up nbapp.close_unsaved_card
# (why, and "Close Without Saving"). Stand in for the card -- a real one
# blocks on dlg.run() -- and answer it both ways.
offered = []
contacts.nbapp.close_unsaved_card = lambda win, exc, path=None: (
    offered.append(path) or False)                 # the person keeps the window
app = bare(True, False)
assert app._on_delete() is True and app.commits == 1
assert offered == [contacts.CONTACTS_FILE]
print("PASS failed final contact edit vetoes close with fields alive, and says why")

contacts.nbapp.close_unsaved_card = lambda win, exc, path=None: True
app = bare(True, False)
assert app._on_delete() is False and app.commits == 1
print("PASS choosing Close Without Saving on the card really closes")
contacts.nbapp.close_unsaved_card = lambda win, exc, path=None: False

app = bare(True, True)
assert app._on_delete() is False and app.commits == 1
print("PASS durable final contact edit allows close")

app = bare(False, False)
assert app._on_delete() is False and app.commits == 0
print("PASS contact list with no open editor closes immediately")

source = open(contacts.__file__, encoding="utf-8").read()
assert 'self.connect("delete-event", self._on_delete)' in source
print("PASS constructor wires close veto before destroy-time fallback")
print("RESULT: PASS")
