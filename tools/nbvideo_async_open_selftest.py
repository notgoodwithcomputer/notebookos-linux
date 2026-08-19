#!/usr/bin/env python3
"""Video preroll must complete from the bus without a blocking state wait."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbvideo  # noqa: E402


class Play:
    def __init__(self):
        self.states = []
        self.get_state_calls = 0
    def set_state(self, state):
        self.states.append(state)
        return FAKE_GST.StateChangeReturn.ASYNC if state == "PAUSED" else "OK"
    def set_property(self, *_): pass
    def get_state(self, *_):
        self.get_state_calls += 1
        raise AssertionError("asynchronous open must not wait")


class FakeGLib:
    @staticmethod
    def timeout_add(_ms, _cb): return 41
    @staticmethod
    def source_remove(_source): pass


FAKE_GST = SimpleNamespace(
    State=SimpleNamespace(NULL="NULL", PAUSED="PAUSED", PLAYING="PLAYING"),
    StateChangeReturn=SimpleNamespace(FAILURE="FAILURE", ASYNC="ASYNC"),
    filename_to_uri=lambda p: "file://" + p,
)


def main():
    old_gst, old_glib = nbvideo.Gst, nbvideo.GLib
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    seen = []
    try:
        nbvideo.Gst, nbvideo.GLib = FAKE_GST, FakeGLib
        p = nbvideo.Playback.__new__(nbvideo.Playback)
        p.available = True
        p._play = Play()
        p._path = None
        p._rate = 1.0
        p._pending_open = None
        p._open_timeout = 0
        p.seek = lambda at, rate=None: at == 1.5 and rate == 1.25
        p.play = lambda: True
        started = p.open_async(path, at=1.5, rate=1.25, play=True,
                               done=seen.append)
        immediate = started and p._play.get_state_calls == 0 and not seen
        p._bus_async_done(None, None)
        completed = seen == [True] and p._pending_open is None
    finally:
        nbvideo.Gst, nbvideo.GLib = old_gst, old_glib
        os.unlink(path)
    for ok, label in ((immediate, "open returns before preroll"),
                      (completed, "matching ASYNC_DONE seeks and plays")):
        print(("PASS" if ok else "FAIL") + ": " + label)
    all_ok = immediate and completed
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
