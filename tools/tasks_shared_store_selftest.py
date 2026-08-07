#!/usr/bin/env python3
"""Headless three-way merge checks for Tasks and the desktop task card."""
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-tasks-shared-home-"))

import tasks  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def write(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)


def rich(title, done=False):
    return {"title": title, "project": None, "due": "today", "date": "",
            "time": "", "prio": 0, "done": done}


with tempfile.TemporaryDirectory(prefix="nb-tasks-shared-") as root:
    tasks.TASKS_FILE = os.path.join(root, "tasks.json")
    tasks.META_FILE = os.path.join(root, "tasks-meta.json")

    # A loaded Tasks window saw both rows incomplete. The widget then ticks
    # Alpha, and Tasks later autosaves after an unrelated edit to Beta.
    baseline = [{"text": "Alpha", "done": False},
                {"text": "Beta", "done": False}]
    write(tasks.TASKS_FILE, baseline)
    app = tasks.Tasks.__new__(tasks.Tasks)
    app.tasks = [rich("Alpha"), rich("Beta")]
    app._flat_base = [dict(row) for row in baseline]
    app._save_warned = False
    write(tasks.TASKS_FILE, [{"text": "Alpha", "done": True},
                             {"text": "Beta", "done": False}])
    app.tasks[1]["prio"] = 2
    app._save_tasks()
    with open(tasks.TASKS_FILE, encoding="utf-8") as fh:
        merged = json.load(fh)
    check(merged[0]["done"] is True,
          "a newer widget tick survives a later Tasks autosave")
    check(app.tasks[0]["done"] is True,
          "the in-memory model adopts the external tick")
    check(app._flat_base == merged,
          "the committed merge becomes the next baseline")

    # A Tasks-side completion edit remains authoritative when the shared file
    # did not change. (For one Boolean field, two writers that both differ from
    # the same baseline necessarily agree; there is no opposite third value.)
    app.tasks[0]["done"] = True
    app._flat_base = [{"text": "Alpha", "done": False},
                      {"text": "Beta", "done": False}]
    write(tasks.TASKS_FILE, [{"text": "Alpha", "done": False},
                             {"text": "Beta", "done": False}])
    app._save_tasks()
    with open(tasks.TASKS_FILE, encoding="utf-8") as fh:
        conflict = json.load(fh)
    check(conflict[0]["done"] is True,
          "a Tasks-side completion edit is not replaced by stale disk state")

    # Duplicate titles are distinct rows in the legacy shared format. The
    # second occurrence can change without changing the first.
    dup = [{"text": "Call", "done": False},
           {"text": "Call", "done": False}]
    app.tasks = [rich("Call"), rich("Call")]
    app._flat_base = [dict(row) for row in dup]
    write(tasks.TASKS_FILE, [{"text": "Call", "done": False},
                             {"text": "Call", "done": True}])
    app._save_tasks()
    with open(tasks.TASKS_FILE, encoding="utf-8") as fh:
        duplicates = json.load(fh)
    check([row["done"] for row in duplicates] == [False, True],
          "duplicate titles merge by occurrence instead of collapsing")

    # Deletion in Tasks is authoritative: the stale widget snapshot must not
    # resurrect a row the full app removed.
    app.tasks = [rich("Keep")]
    app._flat_base = [{"text": "Keep", "done": False},
                      {"text": "Delete", "done": False}]
    write(tasks.TASKS_FILE, [{"text": "Keep", "done": False},
                             {"text": "Delete", "done": True}])
    app._save_tasks()
    with open(tasks.TASKS_FILE, encoding="utf-8") as fh:
        after_delete = json.load(fh)
    check([row["text"] for row in after_delete] == ["Keep"],
          "a widget snapshot cannot resurrect a task deleted in Tasks")

print()
if failures:
    print("TASKS SHARED STORE SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("TASKS SHARED STORE SELFTEST: %d checks, all pass" % checks)
