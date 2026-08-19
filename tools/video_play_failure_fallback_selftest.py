#!/usr/bin/env python3
"""A failed PLAYING transition must leave the still-frame fallback reachable."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import video  # noqa: E402


class Player:
    available = True
    def __init__(self, plays):
        self.plays = plays
        self.stopped = 0
    def open_async(self, *_a, **kw):
        kw["done"](self.plays)
        return True
    def stop(self): self.stopped += 1


class Probe:
    # The path-finding is BORROWED, not stubbed: a clip carries an index into
    # the media bin and nothing else, and a probe that answered "path" from the
    # clip dict itself would agree with a bug the app has no way to make.
    _play_clip_live = video.VideoEditor._play_clip_live
    _clip_media = video.VideoEditor._clip_media
    # getattr, so a build without the method fails BY NAME below instead of
    # blowing up at class-definition time with a traceback
    _clip_path = getattr(video.VideoEditor, "_clip_path",
                         lambda self, clip: None)
    def __init__(self, player):
        self._player = player
        self._live_clip = None
        self._live_pending = None
        self._live_failed = None
        self._playing = True
        self._bin = []
        self.clips = []
        self.shown = []
    def _clip_start(self, _clip): return 0.0
    def _show_video_surface(self, on): self.shown.append(on)


def main():
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        failed_player = Player(False)
        failed = Probe(failed_player)
        media = {"path": path, "name": "shot.mp4", "kind": "video", "dur": 4}
        clip = video._new_clip(0, "video", 4)
        failed._bin = [media]
        failed.clips = [clip]
        took = failed._play_clip_live(0, clip, 0)
        ok1 = (took is True and failed._live_clip is None
               and failed._live_failed == (0, path)
               and failed.shown == [False])
        good_player = Player(True)
        good = Probe(good_player)
        good._bin = [media]
        good.clips = [clip]
        took_good = good._play_clip_live(0, clip, 0)
        ok2 = took_good is True and good._live_clip == 0 and good.shown == [True]
    finally:
        os.unlink(path)
    print(("PASS" if ok1 else "FAIL")
          + ": failed asynchronous open leaves fallback reachable")
    print(("PASS" if ok2 else "FAIL")
          + ": successful PLAYING state alone pins the live clip")
    print("RESULT: %s" % ("ALL PASS" if ok1 and ok2 else "FAILED"))
    return not (ok1 and ok2)


if __name__ == "__main__":
    raise SystemExit(main())
