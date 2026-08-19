#!/usr/bin/env python3
"""Headless forward-compatible Workout store round-trip checks."""
import json
import os
import sys
import tempfile

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import workout

with tempfile.TemporaryDirectory(prefix="workout-persistence-") as td:
    path = os.path.join(td, "workout.json")
    source = {"program": {"week": 2}, "show_widget": False,
              "exercises": [{"id": "e1", "name": "Run", "sets": 3,
                             "reps": 10, "weight": 25}],
              "log": {}, "goals": {}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(source, fh)
    old_store = workout.STORE
    workout.STORE = path
    try:
        app = workout.Workout.__new__(workout.Workout)
        app._load_error = ""
        app._damaged_path = None
        app._quarantine_pending = False
        app.data = app._load()
        app.data["exercises"][0]["reps"] = 12
        app._stamp_today_goal = lambda: None
        saved_ok = app._save()
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    finally:
        workout.STORE = old_store

checks = [
    ("save succeeds", saved_ok),
    ("unknown workout metadata survives", saved.get("program") == {"week": 2}),
    ("unknown exercise metadata survives",
     saved["exercises"][0].get("weight") == 25),
    ("known exercise edit wins", saved["exercises"][0].get("reps") == 12),
]
failed = 0
for label, passed in checks:
    print(("PASS " if passed else "FAIL ") + label)
    failed += not passed
print("%d/%d checks passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
