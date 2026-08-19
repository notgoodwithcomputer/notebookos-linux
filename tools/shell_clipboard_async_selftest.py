#!/usr/bin/env python3
"""The shell must not block on an unresponsive X clipboard owner."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import shell  # noqa: E402


class Clipboard:
    callback = None

    def request_text(self, callback, _data):
        self.callback = callback


def main() -> None:
    obj = shell.Panel.__new__(shell.Panel)
    clip = Clipboard()
    cards = []
    timers = []
    old_timeout, old_remove = shell.GLib.timeout_add, shell.GLib.source_remove
    try:
        shell.GLib.timeout_add = lambda _ms, fn, *args: (timers.append((fn, args)) or 1)
        shell.GLib.source_remove = lambda _source: True
        obj._clipboard = lambda: clip
        obj._card_dialog = lambda heading, body, **kw: cards.append((heading, body))
        obj._show_clipboard()
        assert clip.callback is not None and not cards
        timers[0][0](*timers[0][1])
        assert cards and "could not be read" in cards[0][1]
        clip.callback(clip, "late secret", None)
        assert len(cards) == 1
    finally:
        shell.GLib.timeout_add, shell.GLib.source_remove = old_timeout, old_remove
    print("PASS clipboard reads time out without blocking and ignore late replies")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
