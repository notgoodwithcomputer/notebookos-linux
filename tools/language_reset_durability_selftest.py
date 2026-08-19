#!/usr/bin/env python3
"""Headless regression for failed Language progress reset."""
import copy
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import language  # noqa: E402


def bare(save_ok):
    app = language.Language.__new__(language.Language)
    app.progress = app.norm_progress({
        "goal": 20, "hearts_on": False, "xp": 420, "streak": 9,
        "crowns": {"es": {"basics": 3}}, "seen": ["es:hola"]})
    app._dialog = lambda *_args, **_kwargs: True
    app._save_progress = lambda: save_ok
    app.events = []
    app._refresh_home_stats = lambda: app.events.append("home")
    app._refresh_after_setting = lambda: app.events.append("setting")
    return app


app = bare(False)
before = copy.deepcopy(app.progress)
app._reset_progress()
assert app.progress == before, app.progress
assert app.events == ["home", "setting"], app.events
print("PASS failed reset restores every progress field and refreshes old stats")

app = bare(True)
app._reset_progress()
assert app.progress["xp"] == 0 and app.progress["streak"] == 0
assert app.progress["goal"] == 20 and app.progress["hearts_on"] is False
assert app.events == ["home", "setting"], app.events
print("PASS durable reset clears progress while retaining settings")
print("RESULT: PASS")
