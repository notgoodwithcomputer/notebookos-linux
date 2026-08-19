#!/usr/bin/env python3
"""Shared tasks.json shapes render identically in Tasks and Widgets."""

import json
from pathlib import Path
import sys
import tempfile
import types
import copy
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import tasks  # noqa: E402
import widgets  # noqa: E402


def read(payload):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "tasks.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(tasks, "TASKS_FILE", str(path)), \
                mock.patch.object(widgets, "TASKS_FILE", str(path)):
            owner = tasks.Tasks._read_flat(object())
            card = widgets.Widgets._load_tasks(object())
    return owner, card


def main():
    checks = []
    records = [
        {"text": "Pay rent", "done": False},
        {"text": "Call Sam", "done": True},
        "damaged-record",
    ]
    expected = records[:2]
    owner, card = read(records)
    ok = owner == expected and card == expected
    checks.append(ok); print(("PASS " if ok else "FAIL ") +
                             "list-shaped shared Tasks store")
    owner, card = read({"a": records[0], "b": records[1], "bad": records[2]})
    ok = owner == expected and card == expected
    checks.append(ok); print(("PASS " if ok else "FAIL ") +
                             "keyed shared Tasks store")
    owner, card = read("wrong-shape")
    ok = owner is None and card == []
    checks.append(ok); print(("PASS " if ok else "FAIL ") +
                             "damaged shared Tasks store")

    # A failed persistent write must not strike through/tick a task only in
    # memory and claim success until the next reload exposes the lie.
    visible = [{"text": "Pay rent", "done": False}]
    fake = types.SimpleNamespace(
        tasks=copy.deepcopy(visible),
        _load_tasks=lambda: copy.deepcopy(visible),
        _find_task=widgets.Widgets._find_task,
        _save_tasks=lambda _tasks: False,
        _task_labels={}, _task_checks={},
        _rebuild_tasks=lambda: None, _update_progress=lambda: None,
    )
    widgets.Widgets._toggle_task(fake, 0)
    ok = fake.tasks == visible
    checks.append(ok); print(("PASS " if ok else "FAIL ") +
                             "failed desktop toggle stays visibly incomplete")

    # A stale board snapshot must retain duplicate occurrence identity after a
    # different row is deleted in Tasks between render and click.
    shown = [{"text": "A", "done": False},
             {"text": "B", "done": False},
             {"text": "A", "done": False}]
    disk = [{"text": "A", "done": False},
            {"text": "A", "done": False}]
    saved = []
    fake = types.SimpleNamespace(
        tasks=copy.deepcopy(shown),
        _load_tasks=lambda: copy.deepcopy(disk),
        _find_task=widgets.Widgets._find_task,
        _save_tasks=lambda rows: saved.append(copy.deepcopy(rows)) or True,
        _task_labels={}, _task_checks={},
        _rebuild_tasks=lambda: None, _update_progress=lambda: None,
    )
    widgets.Widgets._toggle_task(fake, 2)
    ok = bool(saved) and [row["done"] for row in saved[0]] == [False, True]
    checks.append(ok); print(("PASS " if ok else "FAIL ") +
                             "stale duplicate toggle keeps occurrence identity")
    passed = sum(checks)
    print("RESULT: %d checks, ALL PASS (%d/%d)" %
          (len(checks), passed, len(checks)) if passed == len(checks) else
          "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
    raise SystemExit(passed != len(checks))


if __name__ == "__main__":
    main()
