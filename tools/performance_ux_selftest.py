#!/usr/bin/env python3
"""Display-free aggregate contracts for UI hot paths and owned work."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def source(name):
    path = DE / name
    text = path.read_text(encoding="utf-8")
    ast.parse(text, filename=str(path))
    return text


def contracts():
    finder = source("finder.py")
    jobs = source("nbjobs.py")
    motion = source("nbmotion.py")
    motion_tree = ast.parse(motion)
    sequencer = source("sequencer.py")
    sysmon = source("sysmon.py")
    widgets = source("widgets.py")
    printer = source("nbprint.py")

    check("_dir_reload_id" in finder and "source_remove(self._dir_reload_id)" in finder,
          "Finder coalesces directory monitor bursts to one owned source")
    closes = ("self._dirgen.close()" in finder
              or ('getattr(self, "_dirgen", None)' in finder
                  and "dirgen.close()" in finder))
    check("self._dirgen.valid(token)" in finder and closes,
          "Finder drops stale reloads and retires them on close")
    check("def discover_printers_async" in printer and "owner.start(" in printer,
          "printer discovery returns through the shared background-job gate")
    timeout_calls = [n for n in ast.walk(motion_tree)
                     if isinstance(n, ast.Attribute) and n.attr == "timeout_add"]
    check("add_tick_callback" in motion and not timeout_calls,
          "animation uses the frame clock, never a private timer loop")
    check("queue_draw_area" in sequencer and
          "def _sync_edit_playhead" in sequencer and
          'self._rendered["edit_head"]' in sequencer,
          "Sequencer playhead supports partial invalidation and editor gating")
    check("def _sync_store" in sysmon and "set_value" in sysmon,
          "System Monitor incrementally synchronizes stable process rows")
    check("_reload_pending" in widgets and "source_remove" in widgets,
          "Widgets coalesces filesystem monitor refreshes")
    check("_UNDO_LIMIT" in source("nbapp.py") and "_UNDO_BUDGET" in source("nbapp.py"),
          "document history is bounded by count and memory budget")
    check("self._threads = [t for t in self._threads if t.is_alive()]" in jobs,
          "shared jobs reap completed worker references")


if __name__ == "__main__":
    contracts()
    print("performance UX selftest: OK")
