#!/usr/bin/env python3
"""Headless contract for the board's process-local timezone refresh."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/widgets.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)

reload_now = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_reload_now")
rollover = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_check_day_rollover")

def calls_apply(node):
    return any(isinstance(n, ast.Attribute)
               and isinstance(n.value, ast.Name)
               and n.value.id == "nbprefs"
               and n.attr == "apply_timezone" for n in ast.walk(node))

checks = {
    "settings store is monitored": "BOARD_FILE, SETTINGS_FILE" in source,
    "coalesced reload applies process timezone": calls_apply(reload_now),
    "day poll backstops missed settings events": calls_apply(rollover),
}
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL"), name)
# Terminal verdict for the release runner: a zero exit with only per-check
# lines is read as DID NOT RUN (run_all_gates SUCCESSWORD), because a suite
# that dies half way prints those lines too.
print("RESULT: %s" % ("ALL PASS" if all(checks.values()) else "FAILED"))
raise SystemExit(not all(checks.values()))
