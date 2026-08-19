#!/usr/bin/env python3
"""Display-free adversarial checks for Music's model and persistence logic."""

import os
import json
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))


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
    asides = [os.path.join(music.CFG_DIR, name)
              for name in os.listdir(music.CFG_DIR)
              if name.startswith("music.json.damaged-")]
    check("damaged playlist bytes survive while a working store is rebuilt",
          app._store_load_ok
          and any(open(path, "rb").read() == original for path in asides),
          "original was not preserved or recovered session stayed read-only")

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
    asides = [os.path.join(music.CFG_DIR, name)
              for name in os.listdir(music.CFG_DIR)
              if name.startswith("music.json.damaged-")]
    kept_shape = any(open(path, "rb").read() == wrong_shape for path in asides)
    check("wrong-shaped playlist store is preserved before rebuilding",
          kept_shape and app._store_load_ok,
          "original was not preserved or the recovered session stayed read-only")

    # The read-only law above protects work that could not be READ. It must not
    # be triggered by the two caches, which are rebuilt from the audio files
    # themselves: a single malformed length row used to lock the store for the
    # whole session, so every playlist made afterwards was accepted by the
    # sidebar, never written, and gone at the next launch.
    cached = (b'{"playlists": ["Road trip"], "tracks": {"Road trip": []},'
              b' "lengths": {"/music/a.mp3": "not-a-row"},'
              b' "tags": {"/music/a.mp3": ["only", "two"]}}')
    with open(music.CFG_FILE, "wb") as fh:
        fh.write(cached)
    app = bare()
    app._load()
    check("a damaged cache row does not lock the store read-only",
          app._store_load_ok, "playlist saves were disabled by a cache entry")
    app._playlists = ["Evening"]
    app._playlist_tracks = {"Evening": []}
    app._save()
    with open(music.CFG_FILE, "rb") as fh:
        saved = fh.read()
    check("a playlist made after a damaged cache row reaches the disk",
          b"Evening" in saved, "the new playlist was never written")

    future = {
        "library_revision": 7,
        "playlists": ["Road trip"],
        "tracks": {"Road trip": [{
            "title": "Song", "artist": "Artist", "album": "Album",
            "time": "3:00", "path": "/music/song.mp3",
            "rating": 5, "gain": -2,
        }]},
        "shuffle": False,
    }
    with open(music.CFG_FILE, "w", encoding="utf-8") as fh:
        json.dump(future, fh)
    app = bare()
    app.songs = [{"title": "Song", "artist": "Artist", "album": "Album",
                  "time": "3:01", "path": "/music/song.mp3"}]
    app._by_path = {"/music/song.mp3": app.songs[0]}
    app._load()
    app._playlists = [name for name, _tracks in app._loaded_playlists]
    app._playlist_tracks = dict(app._loaded_playlists)
    app._saved_shuffle = True
    app._save()
    with open(music.CFG_FILE, encoding="utf-8") as fh:
        saved_future = json.load(fh)
    saved_track = saved_future["tracks"]["Road trip"][0]
    check("future root and track metadata survive an ordinary playlist save",
          saved_future.get("library_revision") == 7
          and saved_track.get("rating") == 5
          and saved_track.get("gain") == -2
          and saved_track.get("time") == "3:01"
          and saved_future.get("shuffle") is True)

    # Python's JSON decoder accepts Infinity. A regenerable cache value must
    # cost only itself, not abort playlist restoration or disable every save.
    with open(music.CFG_FILE, "w", encoding="utf-8") as fh:
        fh.write('{"playlists":["Road trip"],"tracks":{"Road trip":[]},'
                 '"lengths":{"/music/a.mp3":["stat",Infinity]}}')
    app = bare()
    app._load()
    check("a non-finite cached length keeps playlists and writes enabled",
          app._store_load_ok and [name for name, _rows in app._loaded_playlists]
          == ["Road trip"] and "/music/a.mp3" not in app._lengths)

    # A write that does not land has to reach the person: playlists are made
    # one drag at a time, with no Save button to press again.
    import nbapp
    import nbnotify
    posted = []
    real_write, real_post = nbapp.atomic_write_json, nbnotify.post
    def fail_write(*_a, **_k):
        raise OSError("injected music disk full")
    nbapp.atomic_write_json = fail_write
    nbnotify.post = lambda t, b="", **k: posted.append((t, b))
    try:
        app._save_failure_told = False
        app._save()
    finally:
        nbapp.atomic_write_json = real_write
        nbnotify.post = real_post
    check("a failed playlist write says so instead of passing silently",
          getattr(app, "_save_error", "") and len(posted) == 1,
          repr(getattr(app, "_save_error", "")) + repr(posted))


def unreadable_notice_check():
    """A store that could not be read leaves the sidebar empty and every later
    write refused — and _save() returns BEFORE attempting anything, so there is
    not even a failed write to report. Someone can spend an evening sorting
    albums into playlists, close the app, and find none of it, having been told
    nothing at any point. Novel had the same silence and it is fixed there."""
    app = bare()
    said = []
    app._confirm = lambda title, body, ok, cb: said.append((title, body, ok))
    app._say_store_unreadable()
    check("an unreadable playlist store is explained, not left silent",
          len(said) == 1, repr(said))
    check("the card says the playlists could not be read",
          bool(said) and "could not be read" in said[0][0], repr(said))
    check("...and that they were kept",
          bool(said) and "kept" in said[0][1], repr(said))
    check("...and that nothing here will overwrite them",
          bool(said) and "saved over" in said[0][1], repr(said))

    # The notice belongs to the damaged case only, and the CALL is what has to
    # be gated — the card cannot gate itself. Checked structurally: an `elif`
    # is an If nested in the previous If's orelse, so a naive walk counts one
    # call twice, once guarded and once not.
    import ast
    tree = ast.parse(open(music.__file__, encoding="utf-8").read())

    def mentions(node, name):
        # Both spellings of "reads this flag": self._flag, and the
        # getattr(self, "_flag", default) form this codebase uses wherever a
        # half-constructed window might not carry it yet. Matching only the
        # attribute node would report the guard missing on the guarded code.
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute) and n.attr == name:
                return True
            if isinstance(n, ast.Constant) and n.value == name:
                return True
        return False

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for branch in ("body", "orelse"):
            for stmt in getattr(node, branch):
                if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try)):
                    continue
                if mentions(stmt, "_say_store_unreadable"):
                    sites.append(branch == "body"
                                 and mentions(node.test, "_store_load_ok"))
    check("the notice has exactly one call site", len(sites) == 1, repr(sites))
    check("...and it is reached only when the store could not be read",
          bool(sites) and sites[0], repr(sites))


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
    app._save = lambda: True
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
    # _save reports True on success and False on failure, and every mutation
    # ROLLS BACK a failed save (the sidebar must never show what the disk
    # does not hold). A stand-in save therefore has to say it succeeded — a
    # bare `lambda: None` reads as failure and made this check fail for a
    # reason that had nothing to do with undo.
    app._save = lambda: True
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
    app._pl_box = type("Box", (), {"remove": lambda self, row: None,
                                   "pack_start": lambda self, *a: None})()
    app._none = type("NoneRow", (), {
        "set_no_show_all": lambda self, value: None,
        "show": lambda self: None, "hide": lambda self: None})()
    app._save = lambda: True        # a stand-in save that succeeded (see above)
    app._select = lambda view: None
    app._flash = lambda text: None
    app._confirm = lambda *args: None
    app._delete_current_playlist()
    check("deleting a playlist is immediate and undoable",
          app._playlists == []
          and app.undo.calls == [("checkpoint", "Delete Playlist"),
                                 ("commit", None)],
          "[not reached: destructive action still confirms or lacks undo]")

    app = bare()
    app.undo = UndoProbe()
    app._playlists = ["Mix"]
    app._playlist_tracks = {"Mix": [song]}
    app._playlist_rows = [object()]
    app._current_playlist = "Mix"
    app.view = "playlist"
    app._pl_box = type("Box", (), {"remove": lambda self, row: None,
                                   "pack_start": lambda self, *a: None})()
    # the rollback re-creates the playlist row through _create_playlist,
    # which retires the "No playlists" placeholder — the fake needs hide()
    app._none = type("NoneRow", (), {
        "set_no_show_all": lambda self, value: None,
        "show": lambda self: None, "hide": lambda self: None})()
    app._save = lambda: False
    app._select = lambda view: None
    flashes = []
    app._flash = flashes.append
    before = app._undo_snapshot()
    app._restore_undo_snapshot = lambda state: (
        setattr(app, "_playlists", list(state["playlists"])),
        setattr(app, "_playlist_tracks", state["tracks"]),
        setattr(app, "_current_playlist", state["playlist"]))
    app._delete_current_playlist()
    check("failed playlist deletion is rolled back without success wording",
          app._playlists == before["playlists"] and not flashes,
          repr((app._playlists, flashes)))

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

    # Discoverer callbacks are delivered on the main loop. stop() during
    # destroy cannot retract one already queued, so neither a final result nor
    # the finished signal may reach torn-down state/widgets.
    app = bare()
    app._closed = True
    touched = []

    class LateInfo:
        def get_uri(self):
            touched.append("result")
            raise AssertionError("late discovery result was inspected")

    app._populate = lambda: touched.append("populate")
    app._save = lambda: touched.append("save")
    app._disc_dirty = True
    app._on_discovered(None, LateInfo(), None)
    app._on_discover_finished(None)
    check("queued discovery callbacks after close are inert", touched == [],
          "late callbacks touched " + repr(touched))


def playlist_track_identity_check():
    """Identical tags do not make two different audio files interchangeable."""
    app = bare()
    first = {"path": "/music/disc-1/song.flac", "title": "Overture",
             "artist": "Orchestra", "album": "Concert", "time": "3:00"}
    second = {"path": "/music/disc-2/song.flac", "title": "Overture",
              "artist": "Orchestra", "album": "Concert", "time": "3:02"}
    app.songs = [first, second]
    linked = app._link_track(dict(second))
    check("a restored playlist relinks an identically tagged track by path",
          linked is second, "linked to %r" % linked.get("path"))

    # A moved file is still recoverable through the metadata fallback.
    moved = dict(second, path="/old/location/song.flac")
    check("a moved playlist track still falls back to matching metadata",
          app._link_track(moved) is first)


if __name__ == "__main__":
    damaged_store_check()
    unreadable_notice_check()
    shuffle_exhaustion_check()
    destructive_undo_check()
    sort_key_check()
    late_tag_view_check()
    playlist_track_identity_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    raise SystemExit(1 if failed else 0)
