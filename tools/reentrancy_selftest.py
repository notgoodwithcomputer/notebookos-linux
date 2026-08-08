#!/usr/bin/env python3
"""Display-free adversarial checks for app handler re-entrancy."""

import os
import inspect
import json
import threading
import sys
import tempfile
from types import SimpleNamespace

if not os.environ.get("NB_HOME"):
    raise SystemExit("FAIL setup_requires_fresh_nb_home: NB_HOME is not set")

import contacts
import finder
import journal
import music
import nbapp
import novel
import sequencer


class UndoSpy:
    def __init__(self):
        self.checkpoints = 0
        self.commits = 0

    def checkpoint(self, _name):
        self.checkpoints += 1

    def commit(self):
        self.commits += 1


def check(name, fn):
    try:
        fn()
    except Exception as exc:
        print("FAIL %s: %s" % (name, exc))
        return False
    print("PASS %s" % name)
    return True


def double_journal_delete():
    app = SimpleNamespace(entries=[{"id": "A"}, {"id": "B"}], active=0,
                          undo=UndoSpy(), _delete_pending=False)
    app._remove_active = lambda: journal.Journal._remove_active(app)
    app._refresh_list = app._load_active = app._persist = lambda: None
    app._release_delete_guard = lambda: journal.Journal._release_delete_guard(app)
    journal.Journal._delete_active(app)
    journal.Journal._delete_active(app)
    assert [x["id"] for x in app.entries] == ["B"], "second fire deleted shifted entry"
    assert app.undo.checkpoints == 1, "one deletion created multiple undo checkpoints"


def double_contact_delete():
    app = SimpleNamespace(people=[{"id": "A"}, {"id": "B"}], active=0,
                          editing=False, _pending_new=False, _deleted=None,
                          _delete_pending=False)
    app._do_delete = lambda: contacts.Contacts._do_delete(app)
    app._save = app._rebuild_list = app._rebuild_detail = lambda: None
    app._flash = lambda *_a: None
    app._release_delete_guard = lambda: contacts.Contacts._release_delete_guard(app)
    contacts.Contacts._delete_contact(app)
    contacts.Contacts._delete_contact(app)
    assert [x["id"] for x in app.people] == ["B"], "second fire deleted shifted contact"
    assert app._deleted[1]["id"] == "A", "second fire corrupted one-step undo target"


def double_novel_delete_identity():
    chapters = [{"id": "A", "wc": 0, "buffer": object()},
                {"id": "B", "wc": 0, "buffer": object()},
                {"id": "C", "wc": 0, "buffer": object()}]
    app = SimpleNamespace(chapters=chapters, active=0, _total_words=0,
                          undo=UndoSpy())
    app._renumber_chapters = app._refresh_chapter_list = app._recount = lambda: None
    app._show_buffer = app._place_cursor_body = lambda *_a: None
    app._save_state = lambda: True
    target = chapters[0]
    assert "expected_chapter" in inspect.signature(novel.Novel._delete_chapter).parameters, \
        "delete callback has only a mutable index, with no target identity to revalidate"
    novel.Novel._delete_chapter(app, 0, target)
    novel.Novel._delete_chapter(app, 0, target)
    assert [x["id"] for x in app.chapters] == ["B", "C"], "stale index deleted replacement"
    assert app.undo.checkpoints == 1, "one chapter destruction created two undo steps"


def double_music_remove_undo():
    song = {"id": "A"}
    app = SimpleNamespace(_playlist_tracks={"P": [song]}, undo=UndoSpy(),
                          view="songs", _current_playlist=None)
    app._save = lambda: None
    music.Music._remove_from_playlist(app, song, "P")
    music.Music._remove_from_playlist(app, song, "P")
    assert app._playlist_tracks["P"] == [], "removed wrong playlist member"
    assert app.undo.checkpoints == 1, "no-op second removal created an undo checkpoint"


def sequencer_timers_noop_after_destroy():
    touched = []
    app = SimpleNamespace(_closed=True, _save_timer=44, _saved_timer=45,
                          _export={"done": True, "ok": True, "path": "x"},
                          _runner_id=46, transport="play")
    app._save = lambda: touched.append("save")
    app._update_proj = lambda: touched.append("status")
    app._flash = lambda *_a: touched.append("flash")
    assert sequencer.Sequencer._save_timer_fire(app) is False
    assert touched == [], "dispatched autosave touched app state after destroy"
    assert sequencer.Sequencer._saved_restore(app) is False
    assert touched == [], "dispatched status timer touched app state after destroy"
    assert sequencer.Sequencer._export_tick(app) is False
    assert touched == [], "dispatched export poll touched app state after destroy"
    assert sequencer.Sequencer._runner(app) is False
    assert touched == [], "dispatched transport timer touched app state after destroy"


def sequencer_play_stop_play_single_runner():
    calls = []
    app = SimpleNamespace(transport="stop", pos=0.0, length=30.0,
                          _runner_id=None, rec_start=None)
    app._start_audio = lambda: calls.append("audio")
    app._ensure_runner = lambda: (calls.append("runner")
                                  if app._runner_id is None else None)
    def ensure():
        if app._runner_id is None:
            app._runner_id = 77
            calls.append("runner")
    app._ensure_runner = ensure
    app.refresh = lambda: None
    app._stop_transport = lambda *_a: setattr(app, "transport", "stop")
    sequencer.Sequencer._on_play(app)
    sequencer.Sequencer._on_stop(app)
    sequencer.Sequencer._on_play(app)
    assert app.transport == "play", "second Play did not restore play state"
    assert calls.count("runner") == 1, "Play/Stop/Play armed duplicate transport timers"


def atomic_save_race_never_tears_or_duplicates_damage():
    root = tempfile.mkdtemp(prefix="atomic-race-", dir=os.environ["NB_HOME"])
    path = os.path.join(root, "store.json")
    for turn in range(12):
        gate = threading.Barrier(3)
        payloads = ({"turn": turn, "edit": "manual" * 4000},
                    {"turn": turn, "edit": "autosave" * 4000})
        errors = []
        def write(payload):
            try:
                gate.wait()
                nbapp.atomic_write_json(path, payload)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(p,)) for p in payloads]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join()
        assert not errors, "racing atomic writers raised an error"
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        assert stored in payloads, "racing save left a torn or mixed store"
        damaged = [n for n in os.listdir(root) if ".damaged" in n]
        assert damaged == [], "valid racing saves created damaged-file quarantines"


def finder_timers_noop_after_destroy():
    touched = []
    app = SimpleNamespace(_closed=True, _wide_id=9, _wide_gen=2,
                          _searching=False, _typeahead="abc", _typeahead_id=8,
                          _launch_pid=123, status=SimpleNamespace(
                              set_text=lambda *_a: touched.append("status")))
    app._status_text = lambda *_a: touched.append("status-text") or "x"
    app._wide_scan = lambda *_a: None
    app.store = []
    assert finder.Finder._fire_wide_search(app, "q", 2) is False
    assert touched == [], "search debounce touched status after destroy"
    assert finder.Finder._restore_status(app) is False
    assert finder.Finder._clear_typeahead(app) is False
    assert finder.Finder._nudge(app) is False
    assert finder.Finder._launch_watch(app) is False
    assert touched == [], "a Finder timer touched widgets after destroy"


def main():
    checks = (
        ("double_fire_journal_delete_keeps_neighbor", double_journal_delete),
        ("double_fire_contact_delete_preserves_undo", double_contact_delete),
        ("double_fire_novel_delete_revalidates_identity", double_novel_delete_identity),
        ("double_fire_music_remove_single_undo", double_music_remove_undo),
        ("timer_after_destroy_sequencer_callbacks_noop", sequencer_timers_noop_after_destroy),
        ("action_during_async_sequencer_play_stop_play_single_runner",
         sequencer_play_stop_play_single_runner),
        ("save_racing_save_atomic_store_is_complete",
         atomic_save_race_never_tears_or_duplicates_damage),
        ("timer_after_destroy_finder_callbacks_noop",
         finder_timers_noop_after_destroy),
    )
    results = [check(name, fn) for name, fn in checks]
    ok = all(results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
