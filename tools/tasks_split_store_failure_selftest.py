#!/usr/bin/env python3
"""Headless regression for Tasks' two-file commit boundary."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import tasks  # noqa: E402


class Undo:
    def checkpoint(self, _label): pass
    def commit(self): pass


with tempfile.TemporaryDirectory(prefix="tasks-split-") as root:
    tasks.TASKS_FILE = os.path.join(root, "tasks.json")
    tasks.META_FILE = os.path.join(root, "tasks-meta.json")
    row = {"title": "Tax forms", "project": None, "due": "today",
           "date": "2026-08-15", "time": "", "prio": False,
           "done": False, "notes": "keep receipts"}
    with open(tasks.TASKS_FILE, "w", encoding="utf-8") as fh:
        json.dump([{"text": row["title"], "done": False}], fh)
    with open(tasks.META_FILE, "w", encoding="utf-8") as fh:
        json.dump({"tasks": [row], "projects": []}, fh)

    app = tasks.Tasks.__new__(tasks.Tasks)
    app.tasks = [dict(row)]; app._flat_base = [{"text": row["title"],
                                                "done": False}]
    app._meta_extra = {}; app._meta_quarantine_pending = False
    app._save_warned = False; app.undo = Undo(); app.refreshes = 0
    app._close_task_menu = lambda: None
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    app._flash = lambda _text: None

    real_write = tasks.nbapp.atomic_write_json
    failures = {tasks.META_FILE: 1}
    def fail_second(path, data, *args, **kwargs):
        if failures.get(path, 0):
            failures[path] -= 1
            raise OSError("injected sidecar failure")
        return real_write(path, data, *args, **kwargs)
    tasks.nbapp.atomic_write_json = fail_second
    try:
        app._delete_task(0)
    finally:
        tasks.nbapp.atomic_write_json = real_write

    flat = json.load(open(tasks.TASKS_FILE, encoding="utf-8"))
    rich = json.load(open(tasks.META_FILE, encoding="utf-8"))
    ok = (len(app.tasks) == 1 and len(flat) == 1
          and len(rich["tasks"]) == 1 and app.refreshes == 1)
    print(("PASS " if ok else "FAIL ")
          + "failed sidecar delete rolls model and flat projection back")
    print("RESULT: %s" % ("PASS" if ok else "FAILED"))
    raise SystemExit(not ok)
