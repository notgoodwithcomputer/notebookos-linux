#!/usr/bin/env python3
"""Real-use regression drive for Music, on the real widget tree.

    NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \\
        tools/music_realuse_selftest.py

Each check below is something a person did with the app — a library of ripped
files opened, a playlist built and one track pulled out of it again, a search
typed, a playlist renamed to something long, the volume set — driven through
tools/appdrive on an offscreen holder at 1024x740, the smallest panel this OS
supports. Every check is named; a check fails by name, never by crash.

  a numbered file name is not  "01 - Morning Light.wav" read as an artist
  an artist                    called "01", so one album folder appeared as
                               two albums by two numeric artists and the
                               Artist/Album folders it sat in were ignored.
  a search folds accents       "cafe" found nothing while "Café Noir" was on
                               screen; the library already SORTED é and e
                               alike, so the app disagreed with itself.
  every playlist edit is its   New Playlist, Add to Playlist and Rename told
  own undo step                the undo history nothing, so the newest state
                               it held was still the one from launch: a second
                               Ctrl+Z after fixing one mis-removal took the
                               whole session's playlists away and wrote that
                               to disk.
  a long name does not push    a playlist named "Long Drive Home Through The
  the header off-screen        Mountains And Back Again Twice" ran the title
                               to x=980 and pushed the Delete control and the
                               entire search field past the right edge.
  Delete Playlist promises     the menu read "Delete Playlist…", and the
  what it does                 ellipsis promises a card (MENU-CONVENTIONS §1)
                               that never came.
  the volume is remembered     shuffle, repeat and the open playlist all came
                               back at the next launch; the volume slider
                               jumped back to 70 every time.
  a store that could not be    a damaged music.json was moved aside and the
  read says so                 sidebar opened empty with no word said, which
                               reads as an app that lost the playlists.

RED PROOFS, measured, each mutation applied ALONE to de/music.py:

  1. _is_track_number's `if` branch dropped (artist, title = a, t again)
       FAIL a numbered file name is not an artist
       FAIL one album folder is one album
  2. _match back to .lower()
       FAIL an unaccented search finds an accented title
     the three `q = self._fold(self._query)` back to .strip().lower()
       FAIL ...and a search typed WITH the accent finds it as well
     _meta_row's _filter_text back to .lower()
       FAIL an unaccented search finds an accented album row
  3. the checkpoint/commit pairs removed from _new_playlist,
     _add_to_playlist and _apply_rename
       FAIL New Playlist is its own undo step
       FAIL Add to Playlist is its own undo step
       FAIL Rename Playlist is its own undo step
       FAIL a second undo after a removal never empties the playlist
       FAIL ...and the store still holds it
  4. self.title.set_ellipsize(...) removed
       FAIL a long playlist name leaves the search field on screen
       FAIL ...and leaves the Delete Playlist control on screen
  5. the ellipsis put back on "Delete Playlist…"
       FAIL Delete Playlist carries no ellipsis and raises no card
  6. "volume" dropped from the _save payload
       FAIL the volume set last time is the volume at the next launch
  7. the _say_store_quarantined branch removed from __init__
       FAIL a store that could not be read says so at open
"""
import os
import sys
import json
import wave
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="music-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]
HOME = os.path.join(HOME_ROOT, "music")
STORE = os.path.join(HOME, ".config", "notebook", "music.json")
SHOTS = os.environ.get("NB_SHOT_DIR", "")

import appdrive                                                   # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

PANEL_W = 1024
LONG_NAME = "Long Drive Home Through The Mountains And Back Again Twice"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name
          + (("\n     <- %s" % (detail,)) if (detail and not cond) else ""))


def shot(d, base, note=""):
    """A picture, when the driver asked for one (NB_SHOT_DIR)."""
    if SHOTS:
        d.shot(os.path.join(SHOTS, base), note)


def mkwav(rel):
    """A real (silent) audio file in the library, named the way rippers do."""
    path = os.path.join(HOME, "Music", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = wave.open(path, "wb")
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(8000)
    handle.writeframes(b"\x00\x00" * 4000)
    handle.close()
    return path


def fresh(store=None):
    """A home with the same three-album library every check drives."""
    shutil.rmtree(HOME, ignore_errors=True)
    os.makedirs(HOME)
    mkwav("Solar Quartet/Dawn Sessions/01 - Morning Light.wav")
    mkwav("Solar Quartet/Dawn Sessions/02 - Café Noir.wav")
    mkwav("Ella Vane/Night Bus/Ella Vane - Streetlight.wav")
    mkwav("Zoë Park/Café Sessions/Zoë Park - Night Air.wav")
    if store is not None:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        with open(STORE, "w", encoding="utf-8") as fh:
            fh.write(store)
    return appdrive.Drive("music")


def stored():
    if not os.path.exists(STORE):
        return {}
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError:
        return {}


def named(song):
    return (song.get("title"), song.get("artist"), song.get("album"))


def undo_label(app):
    """What the Edit menu offers to undo, and whether it is live."""
    for item in app.menu_items("Edit"):
        if isinstance(item, (tuple, list)):
            return item[0].split("    ")[0], item[1] is not None
    return "", False


def track_rows(app):
    return [r for r in app.songrows.get_children()
            if getattr(r, "_song", None) is not None]


def remove_button(d, row):
    """The row's own trailing control (add in the library, remove in a
    playlist) — the button a person clicks, not a handler called by name."""
    for w in d.walk(row):
        if (isinstance(w, Gtk.Button)
                and "addbtn" in w.get_style_context().list_classes()):
            return w
    return None


# ---------------------------------------------------------------- M-2 -------
def t_ripped_file_names():
    d = fresh()
    try:
        app = d.app
        by_title = {s["title"]: named(s) for s in app.songs}
        check("a numbered file name is not an artist",
              by_title.get("Morning Light")
              == ("Morning Light", "Solar Quartet", "Dawn Sessions"),
              repr(sorted(by_title.values())))
        d.menu_action("View", "Albums")
        d.pump(0.2)
        shot(d, "music_albums.png", "albums after a numbered rip")
        albums = app._rows["albums"]._count_lbl.get_text()
        artists = app._rows["artists"]._count_lbl.get_text()
        check("one album folder is one album",
              (albums, artists) == ("3", "3"),
              "sidebar counts albums %s / artists %s for 3 album folders"
              % (albums, artists))
        check("an 'Artist - Title' name still names its artist",
              by_title.get("Streetlight")
              == ("Streetlight", "Ella Vane", "Night Bus"),
              repr(by_title.get("Streetlight")))
    finally:
        d.close()


# ---------------------------------------------------------------- M-4 -------
def t_search_folds_accents():
    d = fresh()
    try:
        app = d.app
        app._search.grab_focus()
        d.type("cafe")
        d.pump(0.2)
        shot(d, "music_search_cafe.png", "songs search 'cafe'")
        found = [t["title"] for t in app._visible_tracks()]
        check("an unaccented search finds an accented title",
              found == ["Café Noir", "Night Air"] and app._match_count == 2,
              "'cafe' left %r on screen (Café Noir, and Night Air from the "
              "album Café Sessions, are the two in this library)" % (found,))
        app._search.set_text("")
        d.pump(0.1)
        app._search.grab_focus()
        d.type("Café")
        d.pump(0.2)
        check("...and a search typed WITH the accent finds it as well",
              [t["title"] for t in app._visible_tracks()]
              == ["Café Noir", "Night Air"],
              repr([t["title"] for t in app._visible_tracks()]))
        app._search.set_text("")
        d.pump(0.1)
        d.menu_action("View", "Albums")
        d.pump(0.2)
        app._search.grab_focus()
        d.type("cafe")
        d.pump(0.2)
        check("an unaccented search finds an accented album row",
              app._match_count == 1,
              "'cafe' matched %d album rows (Café Sessions is one of them)"
              % app._match_count)
        app._search.set_text("")
        d.pump(0.1)
        app._search.grab_focus()
        d.type("nothing here")
        d.pump(0.2)
        check("a search that matches nothing still matches nothing",
              app._match_count == 0, "matched %d" % app._match_count)
    finally:
        d.close()


# ---------------------------------------------------------------- M-1 -------
def t_playlist_edits_are_undo_steps():
    d = fresh()
    try:
        app = d.app
        app._newpl.clicked()
        d.pump(0.2)
        label, live = undo_label(app)
        check("New Playlist is its own undo step",
              (label, live) == ("Undo New Playlist", True),
              "Edit menu offers %r (live %s) right after New Playlist"
              % (label, live))
        for song in app.songs[:3]:
            app._add_to_playlist(song, app._playlists[0])
            d.pump(0.1)
        label, live = undo_label(app)
        check("Add to Playlist is its own undo step",
              (label, live) == ("Undo Add to Playlist", True),
              "Edit menu offers %r (live %s) after three adds" % (label, live))
        app._playlist_rows[0].clicked()
        d.pump(0.2)
        rows = track_rows(app)
        remove_button(d, rows[1]).clicked()
        d.pump(0.3)
        name = app._playlists[0]
        check("removing one track removes one track",
              len(app._playlist_tracks[name]) == 2,
              repr([t["title"] for t in app._playlist_tracks[name]]))
        d.key("z", ctrl=True)
        d.pump(0.3)
        check("undo of a removal puts that track back",
              len(app._playlist_tracks[name]) == 3,
              repr([t["title"] for t in app._playlist_tracks[name]]))
        d.key("z", ctrl=True)
        d.pump(0.3)
        shot(d, "music_two_undos.png", "after two Ctrl+Z")
        check("a second undo after a removal never empties the playlist",
              app._playlists == [name] and len(app._playlist_tracks[name]) == 2,
              "playlists %r tracks %r"
              % (app._playlists,
                 {k: [t["title"] for t in v]
                  for k, v in app._playlist_tracks.items()}))
        check("...and the store still holds it",
              stored().get("playlists") == [name],
              "store playlists %r" % (stored().get("playlists"),))
        app._apply_rename(name, "Road Trip")
        d.pump(0.2)
        label, live = undo_label(app)
        check("Rename Playlist is its own undo step",
              (label, live) == ("Undo Rename Playlist", True),
              "Edit menu offers %r (live %s) after a rename" % (label, live))
        d.key("z", ctrl=True)
        d.pump(0.3)
        check("undo of a rename gives the playlist its old name back",
              app._playlists == [name], repr(app._playlists))
    finally:
        d.close()


# ---------------------------------------------------------------- M-3 -------
def t_long_playlist_name():
    d = fresh()
    try:
        app = d.app
        app._newpl.clicked()
        d.pump(0.2)
        for song in app.songs[:2]:
            app._add_to_playlist(song, app._playlists[0])
        app._playlist_rows[0].clicked()
        d.pump(0.2)
        app._apply_rename(app._playlists[0], LONG_NAME)
        d.pump(0.3)
        shot(d, "music_long_name.png", "a very long playlist name")
        entry = app._search.get_allocation()
        check("a long playlist name leaves the search field on screen",
              entry.x + entry.width <= PANEL_W,
              "search field at x=%d w=%d on a %dpx panel"
              % (entry.x, entry.width, PANEL_W))
        edges = []
        for button in app._pl_actions.get_children():
            alloc = button.get_allocation()
            edges.append((button.get_tooltip_text(), alloc.x + alloc.width))
        check("...and leaves the Delete Playlist control on screen",
              all(right <= PANEL_W for _tip, right in edges), repr(edges))
        check("...and says the whole name on the header's tooltip",
              app.title.get_tooltip_text() == LONG_NAME,
              repr(app.title.get_tooltip_text()))
        d.menu_action("View", "Songs")
        d.pump(0.2)
        check("a library view carries no leftover tooltip",
              app.title.get_tooltip_text() is None,
              repr(app.title.get_tooltip_text()))
    finally:
        d.close()


# ---------------------------------------------------------------- M-5 -------
def t_delete_playlist_label():
    d = fresh()
    try:
        app = d.app
        app._newpl.clicked()
        d.pump(0.2)
        for song in app.songs[:2]:
            app._add_to_playlist(song, app._playlists[0])
        app._playlist_rows[0].clicked()
        d.pump(0.2)
        name = app._current_playlist
        labels = [it[0] for it in app.menu_items("File")
                  if isinstance(it, (tuple, list))]
        d.menu_action("File", "Delete Playlist")
        d.pump(0.3)
        shot(d, "music_after_delete.png", "right after Delete Playlist")
        check("Delete Playlist carries no ellipsis and raises no card",
              "Delete Playlist" in labels
              and getattr(app, "_dialog_layer", None) is None
              and app._playlists == [],
              "File menu %r, dialog %s, playlists %r"
              % (labels, getattr(app, "_dialog_layer", None) is not None,
                 app._playlists))
        check("...and Edit > Undo brings the playlist and its tracks back",
              undo_label(app) == ("Undo Delete Playlist", True),
              repr(undo_label(app)))
        d.menu_action("Edit", "Undo")
        d.pump(0.3)
        check("...and the undone playlist is on disk again",
              app._playlists == [name]
              and len(app._playlist_tracks.get(name, [])) == 2
              and stored().get("playlists") == [name],
              "playlists %r store %r"
              % (app._playlists, stored().get("playlists")))
    finally:
        d.close()


# ---------------------------------------------------------------- M-7 -------
def t_volume_is_remembered():
    d = fresh()
    try:
        d.app.vol.set_value(35)
        d.pump(0.2)
    finally:
        d.close()
    d = appdrive.Drive("music")
    try:
        check("the volume set last time is the volume at the next launch",
              d.app.vol.get_value() == 35.0,
              "slider reads %r, store volume %r"
              % (d.app.vol.get_value(), stored().get("volume")))
    finally:
        d.close()
    data = stored()
    data["volume"] = "loud"
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    d = appdrive.Drive("music")
    try:
        check("a volume that is not a level opens at the usual one",
              d.app.vol.get_value() == 70.0, repr(d.app.vol.get_value()))
    finally:
        d.close()


# ---------------------------------------------------------------- M-8 -------
def t_damaged_store_says_so():
    for tag, blob in (("wrong shape", '{"playlists": "oops", "tracks": []}'),
                      ("truncated",
                       '{"playlists": ["Road Trip"], "tracks": {"Road Tri')):
        d = fresh(store=blob)
        try:
            app = d.app
            d.pump(0.4)
            shot(d, "music_damaged_%s.png" % tag.split()[0],
                 "launch on a damaged store (%s)" % tag)
            said = [t for t in d.texts()
                    if t.startswith("Your playlists could not be read")]
            aside = [f for f in os.listdir(os.path.dirname(STORE))
                     if ".damaged-" in f]
            check("a store that could not be read says so at open (%s)" % tag,
                  bool(said) and getattr(app, "_dialog_layer", None) is not None
                  and bool(aside),
                  "card %s, kept aside %r, sidebar %r"
                  % (getattr(app, "_dialog_layer", None) is not None,
                     aside, [t for t in d.texts() if "playlist" in t.lower()]))
            app._close_dialog()
            app._newpl.clicked()
            d.pump(0.3)
            check("...and new playlists are still saved (%s)" % tag,
                  stored().get("playlists") == app._playlists != [],
                  "store %r app %r"
                  % (stored().get("playlists"), app._playlists))
        finally:
            d.close()


for fn in (t_ripped_file_names, t_search_folds_accents,
           t_playlist_edits_are_undo_steps, t_long_playlist_name,
           t_delete_playlist_label, t_volume_is_remembered,
           t_damaged_store_says_so):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))

bad = [n for n, ok in RESULTS if not ok]
print("\nRESULT: %s (%d checks, %d failed)"
      % ("PASS" if not bad else "FAILED", len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
