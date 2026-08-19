#!/usr/bin/env python3
"""Headless regression for Packages sort preference failures."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import packages  # noqa: E402


def bare(save_ok):
    app = packages.Packages.__new__(packages.Packages)
    app.sort_field = "name"
    app.sort_desc = False
    app.states = []
    app._update_sort_labels = lambda: app.states.append(
        ("labels", app.sort_field, app.sort_desc))
    app._rebuild_list = lambda: app.states.append(
        ("list", app.sort_field, app.sort_desc))
    app._save_view_prefs = lambda: save_ok
    return app


app = bare(False)
app._on_sort("size")
assert (app.sort_field, app.sort_desc) == ("name", False)
assert app.states[-2:] == [("labels", "name", False),
                           ("list", "name", False)], app.states
print("PASS failed package sort save restores order and arrow")

app = bare(True)
app._on_sort("size")
assert (app.sort_field, app.sort_desc) == ("size", False)
assert len(app.states) == 2, app.states
print("PASS successful package sort save retains new order")

app = bare(True)
app._on_sort("name")
assert (app.sort_field, app.sort_desc) == ("name", True)
print("PASS active package sort still toggles direction")
print("RESULT: PASS")
