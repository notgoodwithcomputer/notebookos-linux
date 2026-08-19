#!/usr/bin/env python3
"""A dead preview backend cannot simulate silent Composer playback."""
import os
import sys
import tempfile
import time
from types import SimpleNamespace

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import composer  # noqa: E402


class Player:
    def poll(self): return 1
    def terminate(self): pass


class Widget:
    def __init__(self): self.text = None; self.playhead_tick = 3
    def set_text(self, value): self.text = value
    def set_label(self, value): self.text = value
    def queue_draw(self): pass


def main():
    old_gst = composer.GST_OK
    composer.GST_OK = False
    fd, path = tempfile.mkstemp(prefix="composer-dead-"); os.close(fd)
    try:
        app = composer.Composer.__new__(composer.Composer)
        app._player = Player(); app._preview_path = path
        app._play_timer = 0; app._play_generation = 1
        app._play_started = time.monotonic(); app._play_duration = 30
        app.staff = Widget(); app.play = Widget(); app.status = Widget()
        app.editor = SimpleNamespace(song={"tempo": 120})
        assert app._play_tick() is False
        assert app._player is None and app.staff.playhead_tick is None
        assert app.play.text == "Play"
        assert app.status.text == "Audio preview is not available."
        assert not os.path.exists(path)
        # A callback captured by an earlier preview cannot attach itself to a
        # replacement player after Stop/Play.
        app._player = Player(); app._play_generation = 3
        assert app._play_tick(2) is False and app._player is not None
        print("PASS dead playback backend stops immediately with an honest error")
        print("RESULT: PASS")
        return 0
    finally:
        composer.GST_OK = old_gst
        try: os.unlink(path)
        except OSError: pass


if __name__ == "__main__":
    raise SystemExit(main())
