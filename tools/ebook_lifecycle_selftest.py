#!/usr/bin/env python3
"""Display-free regression test for the E-book Reader's deferred scroll restore.

Run as:
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=$(mktemp -d) python3 ebook_lifecycle_selftest.py

NO DISPLAY NEEDED, and no Gtk.Window is built: the reader's window class is
driven as plain Python over stub widgets, so this runs on a build host with no X
server. (tools/ebook_selftest.py needs DISPLAY because it constructs the real
window; this one deliberately does not.)

THE BUG THIS EXISTS FOR: _resume_scroll() puts the reader back where they
stopped reading, but only two GLib idle ticks later -- the reading column has to
be allocated before the adjustment reports a real height. Nothing tied that
pending restore to the document it was queued for, and _scroll_to_fraction()
resolves its scroller through _current_scroll(), i.e. whatever is on the reading
surface AT THE MOMENT IT RUNS. Open a book you were three quarters through and
then -- before those idles drain -- open a second one from the Library, and the
first book's restore lands on the second: the new volume opens three quarters of
the way down page one. The same stale restore fires after the open book is
removed from the shelf or the document falls back to the message card.

Checks:
  * a restore whose document is still showing is applied (the feature works),
  * a restore is DROPPED once the reading surface has been swapped,
  * the swap point (_set_reader_widget) is what invalidates it.
"""
import inspect
import os
import sys
import tempfile

os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="ebook-lifecycle-"))

import ebook  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


class FakeAdj(object):
    """Just enough Gtk.Adjustment for _scroll_to_fraction."""

    def __init__(self):
        self.value = 0.0
        self.moves = []

    def get_lower(self):
        return 0.0

    def get_upper(self):
        return 1000.0

    def get_page_size(self):
        return 200.0

    def get_value(self):
        return self.value

    def set_value(self, v):
        self.value = v
        self.moves.append(v)


class FakeScroll(object):
    def __init__(self):
        self.adj = FakeAdj()

    def get_vadjustment(self):
        return self.adj


class FakeSlot(object):
    """Stands in for the reader slot box _set_reader_widget swaps into."""

    def __init__(self):
        self.children = []

    def get_children(self):
        return list(self.children)

    def remove(self, child):
        self.children.remove(child)

    def pack_start(self, child, *a):
        self.children.append(child)


class FakeWidget(object):
    def show_all(self):
        pass


class FakeGLib(object):
    """Records idle callbacks instead of running them, so the test can choose
    the moment they drain -- which is the whole point of the race."""

    def __init__(self):
        self.queue = []
        self._id = 0

    def idle_add(self, cb, *args):
        self.queue.append((cb, args))
        self._id += 1
        return self._id  # must be truthy: _resume_scroll uses `... and False`

    def drain(self, limit=8):
        """Run queued callbacks, including ones they queue in turn."""
        n = 0
        while self.queue and n < limit:
            cb, args = self.queue.pop(0)
            cb(*args)
            n += 1


def new_reader(cls):
    """A bare instance of the reader window with only the attributes the
    scroll-restore path touches. __new__ so no Gtk widget is ever realised."""
    win = cls.__new__(cls)
    win._mode = "epub"
    win._doc_gen = 0
    win._epub_scroll = FakeScroll()
    win._pdf_scroll = None
    win._reader_slot = FakeSlot()
    return win


def main():
    cls = None
    for _, c in inspect.getmembers(ebook, inspect.isclass):
        if c.__module__ == ebook.__name__ and hasattr(c, "_resume_scroll"):
            cls = c
            break
    if cls is None:
        print("FAIL no reader window class found in ebook")
        sys.exit(1)

    real_glib = ebook.GLib
    fake = FakeGLib()
    ebook.GLib = fake
    try:
        # ---- 1. the restore still works when its document is still showing
        win = new_reader(cls)
        cls._resume_scroll(win, {"frac": 0.75})
        fake.drain()
        moved = win._epub_scroll.adj.moves
        # span = upper - page_size - lower = 800; 0.75 -> 600
        check("restore_applied_to_own_document",
              moved == [600.0])

        # ---- 2. the restore is dropped once the surface has been swapped
        win = new_reader(cls)
        cls._resume_scroll(win, {"frac": 0.75})   # book A, three quarters down
        # ...the reader opens book B before the idles drain. The real swap point:
        cls._set_reader_widget(win, FakeWidget())
        win._epub_scroll = FakeScroll()           # book B's fresh scroller
        fake.drain()
        check("stale_restore_dropped_after_book_swap",
              win._epub_scroll.adj.moves == [])
        check("book_swap_advanced_doc_generation", win._doc_gen == 1)

        # ---- 3. the same guard covers the message card (removed / missing file)
        win = new_reader(cls)
        cls._resume_scroll(win, {"frac": 0.5})
        cls._set_reader_widget(win, FakeWidget())  # _show_message goes here too
        win._mode = "message"
        fake.drain()
        check("stale_restore_dropped_after_close",
              win._epub_scroll.adj.moves == [])

        # ---- 4. a restore queued AFTER the swap is honoured (not over-blocked)
        win = new_reader(cls)
        cls._set_reader_widget(win, FakeWidget())
        cls._resume_scroll(win, {"frac": 0.25})
        fake.drain()
        check("fresh_restore_after_swap_still_applied",
              win._epub_scroll.adj.moves == [200.0])

        # ---- 5. a book with no saved fraction queues nothing at all
        win = new_reader(cls)
        cls._resume_scroll(win, {"frac": 0.0})
        cls._resume_scroll(win, {})
        cls._resume_scroll(win, {"frac": "junk"})
        check("no_fraction_queues_no_idle", fake.queue == [])
    finally:
        ebook.GLib = real_glib

    # ---- a PDF page that cannot be fetched must SAY so, not go blank.
    # _pdf_relayout returns early when the page object is None and _pdf_draw
    # then paints white, so pulling the USB stick a book was opened from and
    # pressing Right showed an empty page with no explanation. Both sentences
    # used here already ship with the app.
    import os as _os
    import tempfile as _tf
    import nbstate as _nbstate

    class _Doc:
        def get_page(self, _n):
            raise RuntimeError("the volume went away")

    def _reader_with_missing_page(path):
        w = cls.__new__(cls)
        w._pdf_doc = _Doc()
        w._page = 3
        w._open_path = path
        w._books = [{"path": path, "title": "Anna Karenina", "fmt": "PDF"}]
        w._book_by_path = lambda p: w._books[0]
        w._short_path = lambda p: "~/Books/anna.pdf"
        w.said = []
        w._show_message = lambda *a, **k: w.said.append(a)
        w.relayouts = 0

        def _relayout():
            w.relayouts += 1
        w._pdf_relayout = _relayout
        # The blank path continues past the relayout to queue a scroll-to-top,
        # so the stand-in has to carry those too. Without them the PRE-FIX code
        # dies on AttributeError and this check reports a crash instead of the
        # defect — a red proof that explodes is not a red proof, it just says
        # the fixture was thin.
        w._nav = _nbstate.Generation("reader-fixture")
        w._pdf_scroll = FakeScroll()
        w._scroll_top = lambda *_a: False
        return w

    gone = _os.path.join(_tf.mkdtemp(prefix="ebook-gone-"), "anna.pdf")
    w = _reader_with_missing_page(gone)              # file does NOT exist
    cls._pdf_show_page(w)
    check("a page turn on a vanished PDF says so instead of going blank",
          len(w.said) == 1 and "no longer at that location" in w.said[0][3])
    check("...and does not fall through to the blank relayout",
          w.relayouts == 0)

    here = _tf.mkstemp(prefix="ebook-here-", suffix=".pdf")[1]
    w2 = _reader_with_missing_page(here)             # file DOES exist
    cls._pdf_show_page(w2)
    check("a page that fails while the file is present blames the PDF, "
          "not the storage",
          len(w2.said) == 1 and "could not be opened for rendering"
          in w2.said[0][3])

    # ---- a shelf that cannot be read is not an empty shelf.
    # _load_state used to treat "no file" and "file that will not parse" as the
    # same branch, so a damaged store opened on an empty library and the next
    # page turn wrote that emptiness over twenty books' reading positions.
    import json as _json

    def _reader_with_store(text):
        d = _tf.mkdtemp(prefix="ebook-store-")
        path = _os.path.join(d, "ebook.json")
        if text is not None:
            open(path, "w").write(text)
        real = ebook.CONFIG_PATH
        ebook.CONFIG_PATH = path
        w = cls.__new__(cls)
        w._books = []
        w._open_path = None
        w._state_read_only = False
        try:
            cls._load_state(w)
        finally:
            ebook.CONFIG_PATH = real
        return w, path

    w, path = _reader_with_store('{"books": [] this is not json')
    check("a store that will not parse starts a read-only session",
          w._state_read_only is True)

    # ...and read-only means the bytes stay exactly where the reader left them.
    before = open(path).read()
    real = ebook.CONFIG_PATH
    ebook.CONFIG_PATH = path
    try:
        w._books = [{"path": "/x", "title": "T", "fmt": "PDF"}]
        cls._save_state(w)
    finally:
        ebook.CONFIG_PATH = real
    check("...and nothing is written over the damaged store",
          open(path).read() == before)
    check("...which is still at its own path, not renamed aside",
          _os.path.exists(path)
          and not [n for n in _os.listdir(_os.path.dirname(path))
                   if ".damaged-" in n])

    w2, _ = _reader_with_store(None)
    check("a first run is NOT read-only", w2._state_read_only is False)

    w3, _ = _reader_with_store('{"books": [{"path": "/a", "title": "A", '
                               '"fmt": "PDF"}]}')
    check("a shelf that reads fine is not read-only",
          w3._state_read_only is False and len(w3._books) == 1)

    print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
