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

    # Calendar is an external writer to the schedule rail. Do not admit dates
    # that cannot correspond to a real mini-calendar cell, or loose spellings
    # that violate the shared YYYY-MM-DD schema.
    check(tasks.Tasks._parse_iso("2028-02-29") == (2028, 2, 29),
          "the schedule accepts a canonical leap day")
    check(tasks.Tasks._parse_iso("2026-02-31") is None,
          "the schedule rejects an impossible calendar date")
    check(tasks.Tasks._parse_iso("2026-8-4") is None,
          "the schedule rejects a non-canonical calendar date")
    app.events = []
    malformed = app._event_from_cal(
        {"date": "2026-02-31", "start": 9, "end": 10, "title": "Bad"})
    check(malformed is None,
          "an impossible Calendar record cannot enter the schedule model")

    # A completion is not honest until the authoritative rich sidecar accepts
    # it.  The shared projection is written first, so a failed sidecar write
    # must also be followed by a best-effort projection rollback.
    app.tasks = [rich("Pay rent")]
    app._flat_base = [{"text": "Pay rent", "done": False}]
    write(tasks.TASKS_FILE, app._flat_base)
    undo_events = []
    app.undo = type("Undo", (), {
        "checkpoint": lambda _self, label: undo_events.append(("checkpoint", label)),
        "commit": lambda _self: undo_events.append(("commit", None)),
    })()
    app._flash = lambda _message: None
    real_write = tasks.nbapp.atomic_write_json

    def fail_meta(path, value):
        if path == tasks.META_FILE:
            raise OSError("disk full")
        return real_write(path, value)

    tasks.nbapp.atomic_write_json = fail_meta
    class ToggleProbe:
        def __init__(self):
            self.active = True  # GTK has toggled it before clicked is emitted.
        def set_active(self, value):
            self.active = bool(value)
    toggle = ToggleProbe()
    try:
        app._toggle(toggle, 0)
    finally:
        tasks.nbapp.atomic_write_json = real_write
    with open(tasks.TASKS_FILE, encoding="utf-8") as fh:
        rolled_back = json.load(fh)
    check(app.tasks[0]["done"] is False,
          "a rejected rich-store completion is rolled back in memory")
    check(toggle.active is False,
          "a rejected completion restores the native checked state")
    check(rolled_back[0]["done"] is False,
          "a rejected rich-store completion is rolled back on the desktop")
    check(not any(event[0] == "commit" for event in undo_events),
          "a rejected completion is not recorded as an undoable success")

    # Both projections are part of a successful Tasks save. A rich-sidecar
    # success cannot hide a stale desktop projection.
    notices = []
    app._flash = notices.append
    app._save_warned = False

    def fail_flat(path, value):
        if path == tasks.TASKS_FILE:
            raise OSError("shared store full")
        return real_write(path, value)

    tasks.nbapp.atomic_write_json = fail_flat
    try:
        check(app._save_tasks() is False,
              "a failed shared projection makes the whole save incomplete")
    finally:
        tasks.nbapp.atomic_write_json = real_write
    check(bool(notices), "a failed shared projection is reported visibly")

    # Valid scalar JSON is foreign to the sidecar. If its protective move
    # fails, the launch-time save must not overwrite it.
    write(tasks.META_FILE, "foreign sidecar")
    real_q = tasks.nbapp.quarantine_unrecognized
    tasks.nbapp.quarantine_unrecognized = lambda _path: None
    meta_writes = []
    tasks.nbapp.atomic_write_json = lambda path, value, **_kw: meta_writes.append(path)
    try:
        app._read_meta()
        check(app._save_tasks() is False,
              "a failed sidecar quarantine blocks the replacement save")
    finally:
        tasks.nbapp.quarantine_unrecognized = real_q
        tasks.nbapp.atomic_write_json = real_write
    check(tasks.META_FILE not in meta_writes
          and json.load(open(tasks.META_FILE)) == "foreign sidecar",
          "the foreign sidecar and retry state survive")

print()
if failures:
    print("TASKS SHARED STORE SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("TASKS SHARED STORE SELFTEST: %d checks, all pass" % checks)
