#!/usr/bin/env python3
"""Headless regression for failed Calendar event deletions."""
import os
import sys
from datetime import date

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import calendar as calendar_app  # noqa: E402


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_ok):
    app = calendar_app.Calendar.__new__(calendar_app.Calendar)
    app.events = [
        {"id": "a", "date": date(2026, 8, 17), "title": "Class",
         "series": "s", "repeat": "weekly"},
        {"id": "b", "date": date(2026, 8, 24), "title": "Class",
         "series": "s", "repeat": "weekly"},
    ]
    app.undo = UndoProbe()
    # The real chooser is a modal dialog; it now also takes the body naming the
    # event and a destructive flag, so the stand-in accepts whatever it is passed.
    app._choose_series_scope = lambda *_a, **_k: "all"
    app._save_events = lambda: save_ok
    app.refreshes = 0
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    return app


app = bare(False)
existing = app.events[0]
original_order = list(app.events)
assert app._delete_event(existing) is False
assert app.events == original_order and app.events[0] is existing
assert app.undo.calls == [("checkpoint", "Delete Event")], app.undo.calls
print("PASS failed series deletion restores order and event identity")

app = bare(True)
assert app._delete_event(app.events[0]) is True
assert app.events == []
assert app.undo.calls[-1] == ("commit", None)
print("PASS durable series deletion commits normally")
print("RESULT: PASS")
