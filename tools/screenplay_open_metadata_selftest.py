#!/usr/bin/env python3
"""Regression: named Screenplay JSON keeps extension metadata on open."""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import screenplay  # noqa: E402


class Undo:
    def checkpoint(self, _name): pass
    def commit(self): pass
    def mark_saved(self): pass


class Probe:
    _open_file = screenplay.Screenplay._open_file

    # Open lays a floor under the outgoing script before replacing it (it
    # writes an unsaved, unbound page into Documents so a later close cannot
    # lose it — see new_open_floor_selftest). This probe opens ONTO a bare
    # stand-in with no document of its own, so there is nothing to keep.
    def _keep_outgoing(self):
        return None

    def _say_kept(self, kept):
        pass

    def _fmt_of(self, path):
        return screenplay.Screenplay._fmt_of(self, path)

    def _title_from_path(self, path):
        return screenplay.Screenplay._title_from_path(self, path)

    def __init__(self):
        self.undo = Undo()
        self.loaded = None

    def _set_document(self, *args):
        self.loaded = args

    def _confirm_replace(self, _title):
        return True

    def _flash(self, message):
        raise AssertionError(message)

    def _queue_caret_scroll(self, goal="caret"):
        # Open now puts the desk back at the top of the script it just loaded
        # (the paper stopped scrolling to whatever took focus). This suite is
        # about the metadata that survives the load, so the view is a stub.
        self.scrolled_to = goal


def main():
    root = tempfile.mkdtemp(prefix="screenplay-open-")
    try:
        path = os.path.join(root, "draft.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": "Draft", "subtitle": "", "body": "INT.",
                       "body_tags": [], "path": None,
                       "sync_revision": 12,
                       "production": {"colour": "blue"}}, fh)
        probe = Probe()
        assert probe._open_file(path) is True
        extras = probe.loaded[5]
        assert extras["sync_revision"] == 12
        assert extras["production"] == {"colour": "blue"}
        assert "body" not in extras and "body_tags" not in extras
        print("PASS named screenplay metadata reaches the live document")
        print("RESULT: PASS")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
