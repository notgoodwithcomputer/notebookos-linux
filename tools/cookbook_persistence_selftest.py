#!/usr/bin/env python3
"""Headless forward-compatible Cookbook recipe round-trip checks."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import cookbook

app = cookbook.Cookbook.__new__(cookbook.Cookbook)
source = {
    "cats": ["Soup"], "active_cat": 0, "sel": 0,
    "future_top": {"revision": 4},
    "recipes": [{"title": "Soup", "cat": "Soup", "desc": "old",
                 "nutrition": {"calories": 100}, "recipe_revision": 3}],
}
app._apply_data(source)
app.recipes[0]["desc"] = "edited"
saved = app._serialize()

checks = [
    ("unknown top-level metadata survives", saved["future_top"] == {"revision": 4}),
    ("unknown nested recipe metadata survives",
     saved["recipes"][0]["nutrition"] == {"calories": 100}),
    ("unknown scalar recipe metadata survives",
     saved["recipes"][0]["recipe_revision"] == 3),
    ("known edited recipe fields remain authoritative",
     saved["recipes"][0]["desc"] == "edited"),
]
failed = 0
for label, passed in checks:
    print(("PASS " if passed else "FAIL ") + label)
    failed += not passed
print("%d/%d checks passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
