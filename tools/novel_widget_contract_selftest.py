#!/usr/bin/env python3
"""Display-free Novel/Desktop legacy word-count contract."""
import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-novel-widget-home-"))

import widgets  # noqa: E402


def read(store):
    with tempfile.TemporaryDirectory(prefix="nb-novel-widget-") as root:
        path = os.path.join(root, "novel.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(store, fh)
        old = widgets.NOVEL_FILE
        widgets.NOVEL_FILE = path
        try:
            return widgets.Widgets._read_novel(widgets.Widgets.__new__(widgets.Widgets))
        finally:
            widgets.NOVEL_FILE = old


legacy = read({"title": "Book", "chapters": [{
    "title": "Chapter One", "body": "Chapter One\nhello world"}]})
current = read({"format_version": 2, "title": "Book", "chapters": [{
    "title": "Chapter One", "body": "hello world"}]})
not_mirrored = read({"title": "Book", "chapters": [{
    "title": "Chapter One", "body": "Different heading\nhello world"}]})

checks = [
    (legacy[0] == "2 words", "legacy mirrored titles are not manuscript words"),
    (legacy[0] == current[0], "owner migration and desktop count agree"),
    (not_mirrored[0] == "4 words", "unrelated legacy first lines are preserved"),
]
for ok, message in checks:
    print(("ok   " if ok else "FAIL ") + message)
print()
if not all(ok for ok, _message in checks):
    print("RESULT: FAIL")
    raise SystemExit(1)
print("RESULT: ALL PASS (%d checks)" % len(checks))
