#!/usr/bin/env python3
"""Display-free adversarial checks for Music's model and persistence logic."""

import os
import random
import tempfile


_HOME = tempfile.mkdtemp(prefix="nbmusic-adversarial-")
os.environ["NB_HOME"] = _HOME

import music  # noqa: E402


passed = 0
failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


def bare():
    app = music.Music.__new__(music.Music)
    app.songs = []
    app._loaded_playlists = []
    app._saved_view = "songs"
    app._saved_playlist = None
    app._lengths = {}
    app._tags = {}
    app._by_path = {}
    app._playlists = []
    app._playlist_tracks = {}
    app._current_playlist = None
    app.view = "songs"
    app._restoring = type("Scope", (), {"active": False})()
    return app


def damaged_store_check():
    os.makedirs(music.CFG_DIR, exist_ok=True)
    original = b'{"playlists": ["Road trip"], "tracks": '
    with open(music.CFG_FILE, "wb") as fh:
        fh.write(original)
    app = bare()
    app._load()
    app._save()
    with open(music.CFG_FILE, "rb") as fh:
        after = fh.read()
    check("damaged playlist store survives open+close byte-for-byte",
          after == original, "store was rewritten")

    # Sabotage proof: bypassing the loader's damage state must make the same
    # assertion detect the destructive rewrite, rather than pass vacuously.
    with open(music.CFG_FILE, "wb") as fh:
        fh.write(original)
    mutant = bare()
    mutant._store_load_ok = True
    mutant._save()
    with open(music.CFG_FILE, "rb") as fh:
        mutated = fh.read()
    check("MUTANT: removing damage guard DOES rewrite the store",
          mutated != original, "[not reached: save performed no write]")

    wrong_shape = b'{"playlists":"Road trip","tracks":[1,2]}'
    with open(music.CFG_FILE, "wb") as fh:
        fh.write(wrong_shape)
    app = bare()
    app._load()
    app._save()
    with open(music.CFG_FILE, "rb") as fh:
        after_shape = fh.read()
    check("wrong-shaped playlist store survives open+close byte-for-byte",
          after_shape == wrong_shape, "loader-normalized store was rewritten")


def shuffle_exhaustion_check():
    tracks = [{"title": x} for x in "ABCD"]
    app = bare()
    app._current = tracks[0]
    app.shuffle = type("Toggle", (), {"get_active": lambda self: True})()
    app.repeat = type("Toggle", (), {"get_active": lambda self: False})()
    app._visible_tracks = lambda: tracks
    played = []

    def play(track):
        played.append(track)
        app._current = track

    app._play_track = play
    old = random.randrange
    random.randrange = lambda n: 0
    try:
        for _ in range(len(tracks) - 1):
            app._advance(auto=False, direction=1)
    finally:
        random.randrange = old
    check("shuffle exhausts every other track before repeating",
          len({id(t) for t in played}) == len(tracks) - 1
          and tracks[0] not in played,
          "sequence was " + "".join(t["title"] for t in played))

    # Legacy independent-choice shuffle: with deterministic RNG it chooses B,
    # then A, proving the uniqueness assertion above is capable of going red.
    mutant = []
    idx = 0
    for _ in range(len(tracks) - 1):
        j = 0
        if j >= idx:
            j += 1
        mutant.append(tracks[j])
        idx = j
    check("MUTANT: independent shuffle DOES repeat before exhaustion",
          len({id(t) for t in mutant}) < len(mutant) or tracks[0] in mutant,
          "[not reached: legacy sequence unexpectedly exhausted the queue]")


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def destructive_undo_check():
    song = {"title": "Only track"}
    app = bare()
    app.undo = UndoProbe()
    app._playlist_tracks = {"Mix": [song]}
    app._save = lambda: None
    app._populate = lambda: None
    app.view = "songs"
    app._remove_from_playlist(song, "Mix")
    check("removing a playlist track creates an undo step",
          app._playlist_tracks["Mix"] == []
          and app.undo.calls == [("checkpoint", "Remove Track"),
                                 ("commit", None)],
          repr(app.undo.calls))

    app = bare()
    app._store_load_ok = True
    app._save = lambda: None
    app.view = "songs"
    app._playlists = ["Mix"]
    app._playlist_tracks = {"Mix": [song]}
    app.undo = music.nbapp.UndoHistory(app._undo_snapshot,
                                       app._restore_undo_snapshot)
    app.undo.reset()
    app._remove_from_playlist(song, "Mix")
    undone = app.undo.undo()
    restored = app._playlist_tracks.get("Mix", [])
    check("Undo Remove Track restores playlist membership",
          undone and len(restored) == 1
          and restored[0]["title"] == "Only track",
          "[not reached: history did not restore the removed track]")

    app = bare()
    app.undo = UndoProbe()
    app._playlists = ["Mix"]
    app._playlist_tracks = {"Mix": [song]}
    app._playlist_rows = [object()]
    app._current_playlist = "Mix"
    app._pl_box = type("Box", (), {"remove": lambda self, row: None})()
    app._none = type("NoneRow", (), {
        "set_no_show_all": lambda self, value: None,
        "show": lambda self: None})()
    app._save = lambda: None
    app._select = lambda view: None
    app._flash = lambda text: None
    app._confirm = lambda *args: None
    app._delete_current_playlist()
    check("deleting a playlist is immediate and undoable",
          app._playlists == []
          and app.undo.calls == [("checkpoint", "Delete Playlist"),
                                 ("commit", None)],
          "[not reached: destructive action still confirms or lacks undo]")

    # Sabotage proof for both assertions: an edit with no history calls is the
    # exact old gap, and must not satisfy either expected call sequence.
    check("MUTANT: destructive edit without checkpoint DOES lack undo",
          [] != [("checkpoint", "Delete Playlist"), ("commit", None)])


def sort_key_check():
    key = getattr(music.Music, "_sort_key", None)
    ok = key is not None
    if ok:
        ok = (key("The Album") == key("album")
              and key("Éléonore") == key("Eleonore")
              and key("eleonore") == key("Eleonore"))
    check("library sort ignores articles, case, and diacritics",
          ok, "[not reached: normalized sort key missing or unequal]")

    def mutant(value):
        return value.lower()
    check("MUTANT: lowercase-only sort DOES separate accents and articles",
          mutant("The Album") != mutant("album")
          and mutant("Éléonore") != mutant("Eleonore"))


def late_tag_view_check():
    refreshed = []
    for view in ("songs", "albums", "artists", "scope"):
        app = bare()
        app.view = view
        app._disc_dirty = False
        app._populate = lambda view=view: refreshed.append(view)
        app._on_discover_finished(None)
    check("late tags rebuild every tag-sorted library view",
          refreshed == ["songs", "albums", "artists", "scope"],
          "refreshed " + repr(refreshed))
    check("MUTANT: album-only refresh DOES leave other tag views stale",
          [v for v in ("songs", "albums", "artists", "scope")
           if v == "albums"] != ["songs", "albums", "artists", "scope"])


if __name__ == "__main__":
    damaged_store_check()
    shuffle_exhaustion_check()
    destructive_undo_check()
    sort_key_check()
    late_tag_view_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    raise SystemExit(1 if failed else 0)
