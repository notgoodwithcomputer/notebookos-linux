#!/usr/bin/env python3
"""Headless regression for an old Packages status timer already dispatched."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/packages.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_clear_flash")
namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakePackages:
    _clear_flash = namespace["_clear_flash"]

    def __init__(self):
        self._flash_serial = 2
        self._flash_timer = 22
        self._flash_src = 4
        self.rebuilds = 0

    def _rebuild_detail(self):
        self.rebuilds += 1


app = FakePackages()
# Timer 1 was cancelled, but GLib had already dispatched it. Package selection
# is unchanged, so package identity alone cannot distinguish these messages.
assert app._clear_flash(4, 1) is False
assert app._flash_timer == 22 and app._flash_src == 4 and app.rebuilds == 0

assert app._clear_flash(4, 2) is False
assert app._flash_timer is None and app._flash_src is None and app.rebuilds == 1
print("PASS Packages ignores a dispatched stale status callback")
print("RESULT: PASS")
