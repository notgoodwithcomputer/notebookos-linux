#!/usr/bin/env python3
"""Display-free save-first timezone transaction regression."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/settings.py"


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    commit = methods.get("_commit_timezone")
    if commit is None:
        print("FAIL: timezone choices have no shared save-first transaction")
        return 1
    src = ast.unparse(commit)
    save_at = src.find("self._save_settings()")
    apply_at = src.find("self._apply_tz(iana, posix)")
    required = (save_at >= 0 and apply_at > save_at
                and "source.set_active(old_i)" in src
                and "peer.set_active(old_i)" in src
                and "This could not be saved." in src)
    if not required:
        print("FAIL: timezone failure does not roll back before live apply")
        return 1
    for handler in ("_on_tz", "_on_region_tz"):
        hsrc = ast.unparse(methods[handler])
        if "_commit_timezone" not in hsrc or "_suppress_tz" not in hsrc:
            print("FAIL: %s bypasses the guarded transaction" % handler)
            return 1
    print("PASS: timezone is saved before live apply and both pickers roll back")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
