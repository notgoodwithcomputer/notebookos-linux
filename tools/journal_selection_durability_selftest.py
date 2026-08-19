#!/usr/bin/env python3
"""Headless regression for Journal entry-switch save status."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import journal  # noqa: E402


def bare(save_ok):
    app = journal.Journal.__new__(journal.Journal)
    app.entries = [{"title": "A"}, {"title": "B"}]
    app.active = 0
    app.events = []
    app._save_current = lambda: app.events.append("captured")
    app._refresh_list = lambda: app.events.append("list")
    app._load_active = lambda mark_saved=True: app.events.append(
        ("loaded", mark_saved))
    app._persist = lambda: save_ok
    app._mark_saved = lambda have: app.events.append(("saved", have))
    app._mark_unsaved = lambda: app.events.append("not saved")
    return app


app = bare(False)
app.select_entry(1)
assert app.active == 1
assert ("loaded", False) in app.events and "not saved" in app.events
assert not any(isinstance(e, tuple) and e[0] == "saved" for e in app.events)
print("PASS failed entry switch never paints a Saved claim")

app = bare(True)
app.select_entry(1)
assert app.events[-1] == ("saved", True), app.events
print("PASS durable entry switch paints Saved after persistence")

app = bare(True)
app.select_entry(0)
assert app.events == []
print("PASS selecting the active journal entry remains a no-op")
print("RESULT: PASS")
