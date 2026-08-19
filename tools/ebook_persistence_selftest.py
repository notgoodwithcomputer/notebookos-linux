#!/usr/bin/env python3
"""Headless forward-compatible E-book shelf round-trip checks."""
import json
import os
import sys
import tempfile

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import ebook

with tempfile.TemporaryDirectory(prefix="ebook-persistence-") as td:
    path = os.path.join(td, "ebook.json")
    source = {"sync_revision": 7, "open": "/x.epub", "books": [{
        "path": "/x.epub", "title": "X", "fmt": "epub", "pos": 1,
        "frac": 0.25, "total": 9, "author": "A",
        "highlights": [{"page": 1, "text": "keep"}],
    }]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(source, fh)
    old_path = ebook.CONFIG_PATH
    ebook.CONFIG_PATH = path
    try:
        app = ebook.EbookReader.__new__(ebook.EbookReader)
        app._books = []
        app._open_path = None
        app._store_extra = {}
        app._store_damaged = False
        app._load_state()
        app._books[0]["pos"] = 2
        saved_ok = app._save_state()
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    finally:
        ebook.CONFIG_PATH = old_path

checks = [
    ("save succeeds", saved_ok),
    ("unknown shelf metadata survives", saved.get("sync_revision") == 7),
    ("unknown book metadata survives",
     saved["books"][0].get("highlights") == source["books"][0]["highlights"]),
    ("known reading position edit wins", saved["books"][0].get("pos") == 2),
]
failed = 0
for label, passed in checks:
    print(("PASS " if passed else "FAIL ") + label)
    failed += not passed
print("%d/%d checks passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
