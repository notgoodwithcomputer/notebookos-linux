#!/usr/bin/env python3
"""Headless regression: never signal a PID whose process identity is unknown."""
import ast
import os
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/sysmon.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_do_end")

kills = []
namespace = {
    "proc_start_time": lambda _pid: None,
    "os": types.SimpleNamespace(kill=lambda pid, sig: kills.append((pid, sig))),
    "signal": types.SimpleNamespace(SIGTERM=15),
    "_t": lambda text: text,
}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeMonitor:
    _do_end = namespace["_do_end"]

    def __init__(self):
        self.messages = []

    def _flash(self, message):
        self.messages.append(message)

    def _end_problem(self, _name, _error):
        return "error"


monitor = FakeMonitor()
monitor._do_end(4242, "Writer", None)
assert kills == [], "unknown process identity must never authorize SIGTERM"
assert monitor.messages == ["Writer could not be ended, and is still running"]
print("PASS System Monitor refuses to signal an unidentified PID")
print("RESULT: PASS")
