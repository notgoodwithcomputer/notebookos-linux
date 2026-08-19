#!/usr/bin/env python3
"""Regression: E-book Reader surfaces shelf/position save failures."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import ebook  # noqa: E402


def main():
    app = ebook.EbookReader.__new__(ebook.EbookReader)
    app._books = []
    app._open_path = None
    app._store_extra = {}
    app._store_damaged = False
    notices = []
    old_write = ebook.nbapp.atomic_write_json
    old_note = ebook.nbapp.note_save_failure
    try:
        ebook.nbapp.atomic_write_json = lambda *_a, **_k: (_ for _ in ()).throw(
            OSError(28, "disk full"))
        ebook.nbapp.note_save_failure = lambda owner, exc, path: notices.append(
            (owner, exc.errno, path))
        assert app._save_state() is False
    finally:
        ebook.nbapp.atomic_write_json = old_write
        ebook.nbapp.note_save_failure = old_note
    assert notices == [(app, 28, ebook.CONFIG_PATH)]
    print("PASS failed shelf saves reach the shared visible notification path")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
