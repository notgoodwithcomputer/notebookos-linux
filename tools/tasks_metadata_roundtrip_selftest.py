#!/usr/bin/env python3
"""Regression: Tasks launch migration preserves newer sidecar metadata."""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import tasks  # noqa: E402


class Probe:
    for _name in ("_read_meta", "_read_flat", "_load_tasks", "_norm_task",
                  "_overlay_flat", "_from_flat", "_adopt_orphan_lists",
                  "_load_state", "_save_tasks", "_merge_external_ticks"):
        locals()[_name] = getattr(tasks.Tasks, _name)
    del _name
    _done_by_occurrence = staticmethod(tasks.Tasks._done_by_occurrence)

    def _load_events(self):
        return []

    def _flash(self, _message):
        self.save_error = _message


def main():
    home = tempfile.mkdtemp(prefix="tasks-metadata-")
    old = tasks.TASKS_FILE, tasks.META_FILE
    try:
        cfg = os.path.join(home, ".config", "notebook")
        os.makedirs(cfg)
        tasks.TASKS_FILE = os.path.join(cfg, "tasks.json")
        tasks.META_FILE = os.path.join(cfg, "tasks-app.json")
        with open(tasks.TASKS_FILE, "w", encoding="utf-8") as fh:
            json.dump([{"text": "Pack", "done": False}], fh)
        with open(tasks.META_FILE, "w", encoding="utf-8") as fh:
            json.dump({
                "sync_revision": 7,
                "tasks": [{"title": "Pack", "done": False,
                           "reminder": {"minutes": 15}}],
                "projects": [],
            }, fh)
        probe = Probe()
        probe._load_state()
        outgoing = [{"text": item.get("title", ""),
                     "done": bool(item.get("done"))}
                    for item in probe.tasks]
        probe._merge_external_ticks(outgoing)
        assert probe._save_tasks(), getattr(probe, "save_error", "save failed")
        with open(tasks.META_FILE, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["sync_revision"] == 7
        assert saved["tasks"][0]["reminder"] == {"minutes": 15}
        print("PASS newer sidecar and per-task metadata survives launch save")
        print("RESULT: PASS")
        return 0
    finally:
        tasks.TASKS_FILE, tasks.META_FILE = old
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
