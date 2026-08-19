#!/usr/bin/env python3
"""Display-free fail-closed checks for unrecognized Ebook shelves."""
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-ebook-q-home-"))
import ebook  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-ebook-q-") as root:
    path = os.path.join(root, "ebook.json")
    original = {"books": [{"path": "/only-copy.epub", "title": "My Book"}]}
    Path(path).write_text(json.dumps(original), encoding="utf-8")
    old = ebook.CONFIG_PATH
    ebook.CONFIG_PATH = path
    try:
        app = ebook.EbookReader.__new__(ebook.EbookReader)
        app._books, app._open_path, app._store_damaged = [], None, False
        app._load_state()
        assert app._store_damaged is True and app._books == []

        real_replace = ebook.os.replace
        real_write = ebook.nbapp.atomic_write_json
        writes = []
        ebook.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("read only"))
        ebook.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
        try:
            assert app._save_state() is False
        finally:
            ebook.os.replace = real_replace
            ebook.nbapp.atomic_write_json = real_write
        assert json.loads(Path(path).read_text()) == original
        assert app._store_damaged is True and not writes
        print("PASS failed preservation leaves the only shelf and retry flag intact")

        assert app._save_state() is True
        asides = list(Path(root).glob("ebook.json.damaged-*"))
        assert len(asides) == 1 and json.loads(asides[0].read_text()) == original
        # The rebuilt store carries the reading size too: A-/A+ is a
        # preference the reader set on purpose, so it is persisted with the
        # shelf now and this assertion is the WHOLE payload, not books/open.
        assert json.loads(Path(path).read_text()) == {
            "books": [], "open": None,
            "read_pt": ebook.EbookReader.READ_PT_DEFAULT}
        print("PASS successful preservation keeps old bytes before rebuilding")
    finally:
        ebook.CONFIG_PATH = old

print("RESULT: ALL PASS")
