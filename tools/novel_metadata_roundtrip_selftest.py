#!/usr/bin/env python3
"""Regression: Novel preserves extension metadata through normalization."""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import novel  # noqa: E402


def main():
    source = {
        "format_version": novel.NOVEL_FORMAT_VERSION,
        "title": "Harbour", "author": "Ada", "active": 0,
        "sync_revision": 8,
        "parts": [{"name": "Part One", "colour": "ochre"}],
        "chapters": [{
            "num": "1", "title": "Arrival", "body": "Fog.",
            "ranges": {}, "part": 0,
            "comments": [{"offset": 2, "text": "expand"}],
        }],
    }
    app = novel.Novel.__new__(novel.Novel)
    state = app._parse_state(json.loads(json.dumps(source)))
    assert state is not None
    app._extra = state["_extra"]
    app._title, app._author = state["title"], state["author"]
    app.active, app.doc_path = state["active"], state["doc_path"]
    app.parts = state["parts"]
    app.chapters = [dict(state["chapters"][0], buffer=object())]
    app._buffer_text = lambda _buf: state["chapters"][0]["body"]
    app._buffer_ranges = lambda _buf: state["chapters"][0]["ranges"]
    saved = json.loads(json.dumps(app._serialize()))
    assert saved["sync_revision"] == 8
    assert saved["parts"][0]["colour"] == "ochre"
    assert saved["chapters"][0]["comments"] == [
        {"offset": 2, "text": "expand"}]
    print("PASS book, part and chapter metadata survives reopen and save")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
