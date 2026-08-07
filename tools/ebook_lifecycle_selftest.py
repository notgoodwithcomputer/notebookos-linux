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

    print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
