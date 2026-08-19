#!/usr/bin/env python3
"""Display-free regression for overlapping Finder status flashes."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="finder-status-"))
import finder  # noqa: E402


class Label:
    def __init__(self): self.text = ""
    def set_text(self, text): self.text = text


class Stand:
    _flash_status = finder.Finder._flash_status
    _restore_status = finder.Finder._restore_status
    _status_text = lambda self, count: "%d items" % count

    def __init__(self):
        self.status = Label()
        self.store = [1, 2]
        self._closed = False


callbacks, removed = {}, []
next_id = [0]
real_add, real_remove = finder.GLib.timeout_add, finder.GLib.source_remove


def add(_ms, callback, *args):
    next_id[0] += 1
    callbacks[next_id[0]] = (callback, args)
    return next_id[0]


finder.GLib.timeout_add = add
finder.GLib.source_remove = lambda source: removed.append(source)
try:
    app = Stand()
    app._flash_status("Copied", 2400)
    first = app._status_restore_id
    app._flash_status("This app can't be opened", 6000)
    second = app._status_restore_id
    assert first in removed and second != first
    callbacks[first][0](*callbacks[first][1])
    assert app.status.text == "This app can't be opened"
    callbacks[second][0](*callbacks[second][1])
    assert app.status.text == "2 items"
finally:
    finder.GLib.timeout_add, finder.GLib.source_remove = real_add, real_remove

print("PASS an older restore cannot erase a newer Finder status")
print("RESULT: PASS")
