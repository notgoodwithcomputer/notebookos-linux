#!/usr/bin/env python3
"""A failed damaged-store move must block every shared JSON writer."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbapp  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-atomic-damage-") as root:
    path = os.path.join(root, "store.json")
    original = b'{"unfinished":'
    Path(path).write_bytes(original)
    real_replace = nbapp.os.replace
    nbapp.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("read only"))
    try:
        try:
            nbapp.atomic_write_json(path, {"fresh": True})
        except OSError:
            pass
        else:
            raise AssertionError("damaged bytes were allowed to be replaced")
    finally:
        nbapp.os.replace = real_replace
    assert Path(path).read_bytes() == original
    assert not list(Path(root).glob(".nbw-*.tmp"))
    print("PASS failed preservation blocks replacement and leaves no draft")

print("RESULT: ALL PASS")
