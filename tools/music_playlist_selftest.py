#!/usr/bin/env python3
"""
Headless selftest for music.py "New Playlist" wiring.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/root python3 music_playlist_selftest.py

Validates:
  * the playlist list starts empty
  * calling the new-playlist handler adds "Playlist 1"
  * calling it again adds "Playlist 2"
  * the sidebar gains the corresponding styled row widget(s)
"""
import inspect
import os
import tempfile

# Pin NB_HOME to a throwaway directory BEFORE importing music. Left unset it
# defaults to the developer's real home, where the library scan walks every
# file under it — which is why this test appeared to "hang" for minutes. A
# selftest must never read or write the machine it is run on.
#
# ASSIGNED, not setdefault. guestrun.sh — the documented way to run this —
# exports NB_HOME itself, so setdefault never fired and every run shared one
# home. The playlists this suite creates accumulated there: sixteen of them by
# the time it was noticed, and "playlist list starts empty" had been failing
# for a while on state its own earlier runs left behind. Isolation a caller can
# accidentally switch off is not isolation.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbmusic-selftest-")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import music  # noqa: E402

results = []


def check(name, ok):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


def find_window_class():
    for _, cls in inspect.getmembers(music, inspect.isclass):
        if cls.__module__ == "music" and issubclass(cls, Gtk.Window):
            return cls
    return None


def playlist_rows(win):
    """Row widgets carrying the playlist style class.

    Playlist rows moved OUT of the sidebar box and into their own scroller
    (music.py `_pl_box`) when a library with many playlists was found to grow
    the window past the screen. Walk the whole sidebar subtree rather than its
    direct children, so this keeps testing the behaviour and not the shape of
    one container."""
    rows = []

    def walk(w):
        if w.get_style_context().has_class("playlistrow"):
            rows.append(w)
        if isinstance(w, Gtk.Container):
            for k in w.get_children():
                walk(k)
    walk(win._sb)
    return rows


def row_label_text(row):
    lbl = getattr(row, "_name_lbl", None)
    if lbl is not None:
        return lbl.get_text()
    # fall back to walking the widget tree for a Gtk.Label
    box = row.get_child()
    for w in (box.get_children() if isinstance(box, Gtk.Container) else []):
        if isinstance(w, Gtk.Label):
            return w.get_text()
    return None


def main():
    cls = find_window_class()
    check("locate Music window subclass", cls is not None)
    if cls is None:
        finish()
        return

    win = cls()

    # 1. starts empty
    check("playlist list starts empty", win._playlists == [])
    check("no playlist rows in sidebar at start", len(playlist_rows(win)) == 0)

    # 2. first click adds "Playlist 1"
    win._new_playlist()
    check("list has one entry after first add", len(win._playlists) == 1)
    check("first entry named 'Playlist 1'",
          win._playlists and win._playlists[0] == "Playlist 1")
    rows1 = playlist_rows(win)
    check("sidebar gained one playlist row", len(rows1) == 1)
    check("first row shows 'Playlist 1'",
          bool(rows1) and row_label_text(rows1[0]) == "Playlist 1")
    check("first row reuses viewrow styling",
          bool(rows1) and rows1[0].get_style_context().has_class("viewrow"))

    # 3. second click adds "Playlist 2"
    win._new_playlist()
    check("list has two entries after second add", len(win._playlists) == 2)
    check("second entry named 'Playlist 2'",
          len(win._playlists) == 2 and win._playlists[1] == "Playlist 2")
    rows2 = playlist_rows(win)
    check("sidebar gained a second playlist row", len(rows2) == 2)
    names = sorted(row_label_text(r) for r in rows2)
    check("sidebar rows are 'Playlist 1' and 'Playlist 2'",
          names == ["Playlist 1", "Playlist 2"])

    # 4. rows sit above the "New Playlist" button on screen. They now live in
    # a scroller rather than as siblings of the button, so compare their
    # positions in the window instead of their index in one container.
    def top_of(w):
        ok, _x, y = w.translate_coordinates(win, 0, 0) or (False, 0, 0), 0, 0
        res = w.translate_coordinates(win, 0, 0)
        return res[1] if res else 0
    newpl_y = top_of(win._newpl)
    check("playlist rows sit above the New Playlist button",
          all(top_of(r) <= newpl_y for r in rows2))

    win.destroy()
    finish()


def finish():
    if results and all(results):
        print("RESULT: ALL PASS")
    else:
        print("RESULT: SOME FAILED")


if __name__ == "__main__":
    main()
