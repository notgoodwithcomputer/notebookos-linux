#!/usr/bin/env python3
"""Headless regression for Writer close-time recovery failure."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import writer  # noqa: E402


def bare(save_ok, path=None, dirty=True):
    app = writer.Writer.__new__(writer.Writer)
    app._save_autosave = lambda: save_ok
    app._path = path
    app._file_dirty = dirty
    return app


# The veto is never silent: a refused write puts up nbapp.close_unsaved_card,
# which says why and offers "Close Without Saving". Stand in for the card so
# the check can answer it both ways -- a real card blocks on dlg.run().
offered = []
writer.nbapp.close_unsaved_card = lambda win, exc, path=None: (
    offered.append((exc, path)) or False)          # the person keeps the window

assert bare(False)._on_delete() is True
assert offered and offered[-1][1] == writer.DOC_FILE
print("PASS failed recovery write vetoes close for an unsaved document, and says why")

assert bare(False, "/docs/book.writer", True)._on_delete() is True
print("PASS failed recovery write vetoes close for dirty named document")

writer.nbapp.close_unsaved_card = lambda win, exc, path=None: True   # they chose to close
assert bare(False)._on_delete() is False
print("PASS choosing Close Without Saving on the card really closes")
writer.nbapp.close_unsaved_card = lambda win, exc, path=None: False

assert bare(False, "/docs/book.writer", False)._on_delete() is False
print("PASS clean named document can close without private recovery")

assert bare(True)._on_delete() is False
print("PASS successful close-time recovery allows close")

source = open(writer.__file__, encoding="utf-8").read()
assert 'self.connect("delete-event", self._on_delete)' in source
print("PASS constructor wires the veto before destruction")
print("RESULT: PASS")
