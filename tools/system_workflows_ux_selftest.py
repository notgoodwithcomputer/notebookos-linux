#!/usr/bin/env python3
"""Headless acceptance checks for Settings and USB Writer workflow wiring."""

import ast
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import nbjobs  # noqa: E402
import nbstate  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def settings_restore_contract():
    panes = ["System", "Displays", "Accessibility"]
    check(nbstate.choice("Accessibility", panes, "System") == "Accessibility",
          "a valid last Settings pane is restored")
    check(nbstate.choice("Removed Pane", panes, "System") == "System" and
          nbstate.choice(42, panes, "System") == "System",
          "missing or malformed pane state falls back to System")
    scope = nbstate.RestoreScope()
    writes = []
    with scope:
        if not scope.active:
            writes.append("write")
    check(writes == [], "restoring a Settings pane does not save preferences")


class _Queue:
    def __init__(self):
        self.items = []

    def __call__(self, fn):
        self.items.append(fn)
        return True

    def drain(self):
        while self.items:
            self.items.pop(0)()


def task_lifecycle_contract():
    queue = _Queue()
    owner = nbjobs.JobOwner(dispatch=queue, name="workflow")
    gate = threading.Event()
    delivered = []

    def work(job):
        job.progress(.25, "writing")
        gate.wait(2)
        job.checkpoint()
        return "done"

    first = owner.start("write", work,
                        on_progress=lambda f, p: delivered.append((f, p)),
                        on_done=lambda value: delivered.append(value),
                        on_cancel=lambda: delivered.append("cancelled"),
                        policy=nbjobs.REJECT)
    second = owner.start("write", work, policy=nbjobs.REJECT)
    check(first is not None and second is None,
          "an in-flight destructive task rejects duplicate submission")
    owner.close()
    gate.set()
    owner.join(2)
    queue.drain()
    check(delivered == [],
          "closing a workflow rejects queued progress and completion")


def wiring_contract():
    settings_path = DE / "settings.py"
    writer_path = DE / "usbwriter.py"
    settings = settings_path.read_text(encoding="utf-8")
    writer = writer_path.read_text(encoding="utf-8")
    ast.parse(settings, filename=str(settings_path))
    ast.parse(writer, filename=str(writer_path))
    check("nbstate.choice(" in settings and
          "with self._restore:" in settings and
          "if not self._restore.active:" in settings and
          "self._pager.switch(name)" in settings,
          "Settings validates/restores panes through shared state and transitions")
    check("self._jobs.start(" in writer and
          "policy=nbjobs.REJECT" in writer and
          "job.checkpoint()" in writer and
          "self._jobs.cancel(\"write\")" in writer and
          "self._jobs.close()" in writer,
          "USB Writer uses shared progress, duplicate, cancellation and close gates")
    check("threading.Thread" not in writer and "GLib.idle_add(self._finished" not in writer,
          "USB Writer no longer bypasses the shared job delivery gate")


if __name__ == "__main__":
    settings_restore_contract()
    task_lifecycle_contract()
    wiring_contract()
    print("system workflows UX selftest: OK")
