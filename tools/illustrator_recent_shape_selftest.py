#!/usr/bin/env python3
"""Headless regression for foreign Illustrator preference shapes."""
import glob
import json
import os
import shutil
import sys
import tempfile

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import illustrator  # noqa: E402

tmp = tempfile.mkdtemp(prefix="illustrator-recent-shape-")
try:
    illustrator.CFG_FILE = os.path.join(tmp, "illustrator.json")
    original = b'["settings-from-a-newer-version"]\n'
    with open(illustrator.CFG_FILE, "wb") as fh:
        fh.write(original)

    app = illustrator.Illustrator.__new__(illustrator.Illustrator)
    app._recent = []
    app._sync_recent = lambda: None
    app._remember("#123456")

    asides = glob.glob(illustrator.CFG_FILE + ".damaged-*")
    assert len(asides) == 1, asides
    with open(asides[0], "rb") as fh:
        assert fh.read() == original
    with open(illustrator.CFG_FILE, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["recent"] == ["#123456"], saved
    # Terminal verdict for the release runner (run_all_gates SUCCESSWORD): a
    # stream of PASS lines with a zero exit is not a report it will trust —
    # a suite that dies half way prints those too.
    print("PASS foreign preference shape is preserved before recent-color save")
    print("RESULT: ALL PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
