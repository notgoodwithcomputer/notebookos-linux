#!/usr/bin/env python3
"""Headless acceptance checks for the Hour 6 document-safety contracts."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import nbapp  # noqa: E402
import screenplay  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def atomic_failure_preserves_old_bytes():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "draft.txt"
        path.write_text("finished draft", encoding="utf-8")
        with mock.patch.object(nbapp.os, "replace", side_effect=OSError("full")):
            try:
                nbapp.atomic_write_text(str(path), "new draft")
            except OSError:
                pass
            else:
                raise AssertionError("atomic write failure was hidden")
        check(path.read_text(encoding="utf-8") == "finished draft",
              "failed atomic write preserves the prior file")
        check(not list(Path(td).glob(".nbw-*.tmp")),
              "failed atomic write removes its temporary file")


def malformed_store_is_quarantined():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "store.json"
        bad = b'{"unfinished":'
        path.write_bytes(bad)
        quarantined = nbapp.preserve_damaged(str(path))
        check(quarantined is not None and not path.exists(),
              "malformed store is moved aside before a later save")
        check(Path(quarantined).read_bytes() == bad,
              "quarantine preserves the malformed bytes exactly")
        nbapp.atomic_write_json(str(path), {"safe": True})
        check(json.loads(path.read_text(encoding="utf-8")) == {"safe": True},
              "a clean replacement can be saved after quarantine")


def saved_checkpoint_tracks_undo_and_redo():
    state = {"body": "original", "_caret": 0}
    restored = []
    history = nbapp.UndoHistory(lambda: dict(state),
                                lambda snap: restored.append(snap["body"]))
    history.reset()
    history.mark_saved()
    check(not history.is_dirty(), "saved checkpoint begins clean")
    state["body"] = "edited"
    history.checkpoint("Edit")
    history.commit()
    check(history.is_dirty(), "an edit after the checkpoint is dirty")
    history.undo()
    check(not history.is_dirty(), "undoing to saved content is clean")
    history.redo()
    check(history.is_dirty(), "redoing the edit is dirty again")


class _Entry:
    def __init__(self, value):
        self.value = value

    def get_text(self):
        return self.value

    def set_text(self, value):
        self.value = value


class _UndoSentinel:
    def __init__(self):
        self.saved = False

    def mark_saved(self):
        self.saved = True


class _FakeScreenplay:
    _file_save_as = screenplay.Screenplay._file_save_as
    _set_identity = screenplay.Screenplay._set_identity
    _title_from_path = screenplay.Screenplay._title_from_path

    def __init__(self, chosen):
        self._path = "/documents/old.json"
        self._file_dirty = True
        self._loading = False
        self.scripttitle = _Entry("Old title")
        self.undo = _UndoSentinel()
        self.chosen = chosen
        self.flashes = []

    def _choose_file(self, save=False):
        return self.chosen

    def _write_file(self, path):
        return False

    def _update_status(self):
        pass

    def _flash(self, message):
        self.flashes.append(message)


def save_as_failure_rolls_back_identity():
    doc = _FakeScreenplay("/documents/new.json")
    doc._file_save_as()
    check(doc._path == "/documents/old.json" and
          doc.scripttitle.get_text() == "Old title",
          "failed Save As restores the prior path and title")
    check(doc._file_dirty and not doc.undo.saved,
          "failed Save As preserves dirty and saved-checkpoint state")
    # The app moved from the fixed "Save failed" to nbapp.save_failure_reason
    # sentences — pin the CONTRACT (exactly one flash, a real capitalised
    # sentence, no exception text), not one spelling of it.
    check(len(doc.flashes) == 1 and bool(doc.flashes[0])
          and doc.flashes[0][0].isupper() and doc.flashes[0].endswith(".")
          and "Error" not in doc.flashes[0] and "errno" not in doc.flashes[0],
          "failed Save As reports one plain-language failure")


if __name__ == "__main__":
    atomic_failure_preserves_old_bytes()
    malformed_store_is_quarantined()
    saved_checkpoint_tracks_undo_and_redo()
    save_as_failure_rolls_back_identity()
    print("document safety selftest: OK")
