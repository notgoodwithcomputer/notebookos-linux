#!/usr/bin/env python3
"""Headless regression for failed E-book shelf/open persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import ebook  # noqa: E402


class Probe:
    FORMATS = ebook.EbookReader.FORMATS
    _open_book = ebook.EbookReader._open_book
    _add_book = ebook.EbookReader._add_book
    _book_by_path = ebook.EbookReader._book_by_path

    def __init__(self, save_ok):
        self._books = [{"path": "/books/current.pdf", "title": "Current",
                        "fmt": "PDF", "pos": 4, "author": ""}]
        self._open_path = "/books/current.pdf"
        self.save_ok = save_ok
        self.shown = []
        self.shelf_refreshes = 0
    def _remember_pos(self, force=False): pass
    def _save_state(self): return self.save_ok
    def _show_current(self): self.shown.append(self._open_path)
    def _populate_shelf(self): self.shelf_refreshes += 1


failed = Probe(False)
before = copy.deepcopy(failed._books)
passed = Probe(True)
checks = [
    (failed._open_book("/books/new.pdf") is True
     and failed._books == before
     and failed._open_path == "/books/current.pdf",
     "failed shelf write restores library and open-book identity"),
    (failed.shown == ["/books/current.pdf"] and failed.shelf_refreshes == 1,
     "failed open redraws the durable reading surface and shelf"),
    (passed._open_book("/books/new.pdf") is True
     and passed._open_path == "/books/new.pdf"
     and passed._books[0]["path"] == "/books/new.pdf",
     "successful supported-book open remains committed"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
