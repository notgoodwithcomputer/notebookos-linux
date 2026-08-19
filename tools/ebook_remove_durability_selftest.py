#!/usr/bin/env python3
"""Headless regression for failed E-book Reader shelf removal."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import ebook  # noqa: E402


def bare(save_ok):
    app = ebook.EbookReader.__new__(ebook.EbookReader)
    app._books = [{"path": "/books/a.epub", "title": "A", "pos": 12},
                  {"path": "/books/b.pdf", "title": "B", "pos": 3}]
    app._open_path = "/books/a.epub"
    app.events = []
    app._close_confirm = lambda: app.events.append("confirm closed")
    app._save_state = lambda: save_ok
    app._show_empty = lambda: app.events.append("empty")
    app._populate_shelf = lambda: app.events.append("shelf")
    return app


app = bare(False)
original = app._books
app._remove_book(app._books[0])
assert app._books is original and app._open_path == "/books/a.epub"
assert app.events == ["confirm closed", "shelf"], app.events
print("PASS failed shelf removal preserves open book and exact library")

app = bare(True)
app._remove_book(app._books[0])
assert [b["title"] for b in app._books] == ["B"], app._books
assert app._open_path is None
assert app.events == ["confirm closed", "empty", "shelf"], app.events
print("PASS durable open-book removal commits and shows empty reader")

app = bare(True)
app._open_path = "/books/a.epub"
app._remove_book(app._books[1])
assert app._open_path == "/books/a.epub" and "empty" not in app.events
print("PASS removing a closed book keeps the current document visible")
print("RESULT: PASS")
