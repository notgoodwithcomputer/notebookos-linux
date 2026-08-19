#!/usr/bin/env python3
"""New/Edit Exercise are named structural Undo steps."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/workout.py"


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    failed = 0
    for name, label in (("_new_exercise", "New Exercise"),
                        ("_edit_exercise", "Edit Exercise")):
        src = ast.unparse(methods[name])
        checkpoint = "self.undo.checkpoint('%s')" % label
        ok = (checkpoint in src and "self.undo.commit()" in src
              and src.find(checkpoint) < src.find("self._save_or_rollback(before)")
              < src.find("self.undo.commit()"))
        print(("PASS: " if ok else "FAIL: ") + label + " is an atomic undo step")
        failed += not ok
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
