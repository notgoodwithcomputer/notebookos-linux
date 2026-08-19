#!/usr/bin/env python3
"""A wholesale new synonym cannot redefine the agreed product term."""

from pathlib import Path
import tempfile
import shutil
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "de"
        shutil.copytree(DE, copy)
        path = copy / "lang_es.json"
        cat = json.loads(path.read_text(encoding="utf-8"))
        for key in list(cat):
            if "tile" in key.lower():
                cat[key] = "azulejo"
        path.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
        # The script's DE is fixed to the checkout, so execute a patched copy.
        source = (ROOT / "tools/term_consistency_check.py").read_text(encoding="utf-8")
        source = source.replace('DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",\n                  "rootfs-overlay", "opt", "notebook", "de")',
                                'DE = %r' % os.fspath(copy))
        checker = Path(td) / "check.py"
        checker.write_text(source, encoding="utf-8")
        proc = subprocess.run(["python3", os.fspath(checker)], capture_output=True, text=True)
        assert proc.returncode != 0 and "agreed root" in proc.stdout
    print("PASS unknown wholesale terminology drift fails the canonical-root gate")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
