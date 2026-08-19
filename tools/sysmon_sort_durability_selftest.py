#!/usr/bin/env python3
"""Headless regression for System Monitor sort persistence."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import sysmon  # noqa: E402


class Store:
    def __init__(self):
        self.calls = []

    def set_sort_column_id(self, column, order):
        self.calls.append((column, order))


class Arrow:
    """The header arrow image _update_sort_arrows points at the active column.

    This stub used to be a Gtk.TreeViewColumn with set_sort_indicator /
    set_sort_order, because the arrow used to be GTK's own sort indicator. It
    is now an image the app packs into its own header widget (GTK packs the
    indicator at the FAR end of the header button, which stranded the arrow
    600px from the word NAME, and shows/hides it, which changed the column
    widths on every re-sort). The claim under test has not moved: when the
    preference cannot be saved, the arrow a reader sees goes back to the
    column the next launch will actually sort by."""

    def __init__(self):
        self.active = None
        self.order = None

    def set_from_surface(self, _surface):
        self.active = True

    def clear(self):
        self.active = False


def bare(save_ok):
    app = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
    app._sort_col = 0
    app._sort_order = sysmon.Gtk.SortType.ASCENDING
    app.store = Store()
    app._sort_widgets = {0: Arrow(), 5: Arrow()}
    app._save_sort_prefs = lambda: save_ok
    return app


app = bare(False)
app._apply_sort(5, sysmon.Gtk.SortType.DESCENDING)
assert (app._sort_col, app._sort_order) == (
    0, sysmon.Gtk.SortType.ASCENDING)
assert app.store.calls == [
    (5, sysmon.Gtk.SortType.DESCENDING),
    (0, sysmon.Gtk.SortType.ASCENDING)], app.store.calls
assert app._sort_widgets[0].active is True
assert app._sort_widgets[5].active is False
print("PASS failed sort save restores table ordering and active arrow")

app = bare(True)
app._apply_sort(5, sysmon.Gtk.SortType.DESCENDING)
assert app._sort_col == 5
assert app.store.calls == [(5, sysmon.Gtk.SortType.DESCENDING)]
assert app._sort_widgets[5].active is True
print("PASS successful sort save retains table ordering and arrow")
print("RESULT: PASS")
