#!/usr/bin/env python3
"""A sidecar failure cannot roll back another writer's completion tick."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import tasks  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="tasks-external-tick-") as td:
        old_paths = tasks.TASKS_FILE, tasks.META_FILE
        old_projects = copy.deepcopy(tasks.PROJECTS)
        original_write = tasks.nbapp.atomic_write_json
        try:
            tasks.TASKS_FILE = os.path.join(td, "tasks.json")
            tasks.META_FILE = os.path.join(td, "tasks-app.json")
            Path(tasks.TASKS_FILE).write_text(
                json.dumps([{"text": "Read", "done": True}]),
                encoding="utf-8")
            app = tasks.Tasks.__new__(tasks.Tasks)
            app.tasks = [{"title": "Read", "done": False,
                          "due": "today", "project": "Inbox"}]
            app._flat_base = [{"text": "Read", "done": False}]
            app._meta_extra = {}
            app._meta_quarantine_pending = False
            app._save_warned = False
            app._flash = lambda _message: None
            app.view = "view:today"
            before = app._undo_snapshot()
            app.tasks[0]["due"] = "tomorrow"

            def write(path, value):
                if path == tasks.META_FILE:
                    raise OSError("sidecar full")
                return original_write(path, value)

            tasks.nbapp.atomic_write_json = write
            assert not app._save_tasks_or_restore(before)
            assert app.tasks[0]["due"] == "today"
            assert app.tasks[0]["done"] is True
            saved = json.loads(Path(tasks.TASKS_FILE).read_text(encoding="utf-8"))
            assert saved == [{"text": "Read", "done": True}]
            assert app._flat_base == saved
            print("PASS failed rich edit rolls back without losing widget tick")
            print("RESULT: PASS")
            return 0
        finally:
            tasks.nbapp.atomic_write_json = original_write
            tasks.TASKS_FILE, tasks.META_FILE = old_paths
            tasks.PROJECTS[:] = old_projects


if __name__ == "__main__":
    raise SystemExit(main())
