#!/usr/bin/env python3
"""Display-free acceptance checks for Media transport lifecycle truth."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import media  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


class _Player:
    def __init__(self):
        self.calls = []

    def seek_simple(self, *args):
        self.calls.append(("seek", args))

    def set_state(self, state):
        self.calls.append(("state", state))


class _Fake:
    _on_video_eos = media.MediaViewer._on_video_eos
    _on_video_error = media.MediaViewer._on_video_error
    _on_video_poll = media.MediaViewer._on_video_poll
    _start_video_poll = media.MediaViewer._start_video_poll

    def __init__(self, closed=False, path="clip.mp4"):
        self._closed = closed
        self._v_path = path
        self._player = _Player()
        self._v_poll_id = 0
        self._v_playing = True
        self.notices = []

    def _set_video_glyph(self, value):
        self.glyph = value

    def _set_seek(self, value):
        self.seek_value = value

    def _stop_video(self):
        self._v_path = None
        self.stopped = True

    def _show_notice(self, *message):
        self.notices.append(message)


def lifecycle_contract():
    closed = _Fake(closed=True)
    closed._on_video_eos()
    closed._on_video_error(None, None)
    check(closed._player.calls == [] and closed.notices == [],
          "late EOS/error after destroy cannot touch transport or UI")
    check(closed._on_video_poll() is False and closed._v_poll_id == 0,
          "a poll dispatched after close retires itself")

    stopped = _Fake(path=None)
    stopped._on_video_eos()
    stopped._on_video_error(None, None)
    check(stopped._player.calls == [] and stopped.notices == [],
          "late messages from a replaced/stopped clip are discarded")

    live = _Fake()
    live._on_video_eos()
    check([call[0] for call in live._player.calls] == ["seek", "state"] and
          live._v_playing is False and live.glyph == "play" and
          live.seek_value == 0,
          "a real EOS truthfully rewinds and changes transport to paused")


def wiring_contract():
    path = DE / "media.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    methods = {node.name: node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    destroy = ast.get_source_segment(source, methods["_on_destroy"]) or ""
    check(destroy.find("self._closed = True") < destroy.find("self._stop_video()"),
          "Media raises its lifetime gate before pipeline teardown")
    for name in ("_on_video_toggle", "_on_video_eos", "_on_video_error",
                 "_start_video_poll", "_on_video_poll"):
        body = ast.get_source_segment(source, methods[name]) or ""
        check("self._closed" in body, "%s checks the closed owner" % name)
    check("self._flush_video_bus()" in source and "self._v_path = None" in source,
          "clip replacement flushes queued messages and stop clears identity")


if __name__ == "__main__":
    lifecycle_contract()
    wiring_contract()
    print("media/creative UX selftest: OK")
    print("RESULT: PASS")
