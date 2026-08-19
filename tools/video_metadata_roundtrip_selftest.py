#!/usr/bin/env python3
"""Regression: Video preserves newer nested project metadata."""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import video  # noqa: E402


def main():
    source = {
        "version": 2, "size": [video.EXPORT_W, video.EXPORT_H],
        "sync_revision": 11,
        "bin": [{"path": "/tmp/shot.mp4", "name": "Shot",
                 "kind": "video", "dur": 8, "srcdur": 8.0,
                 "proxy": {"scale": 0.5}}],
        "clips": [{"media": 0, "kind": "video", "duration": 4,
                   "colour_grade": {"temperature": 12}}],
        "music": {"path": "/tmp/theme.wav", "name": "Theme",
                  "volume": 0.6, "fadein": True, "fadeout": True,
                  "beat_grid": [0.0, 0.5]},
    }
    app = video.VideoEditor.__new__(video.VideoEditor)
    app._apply_data(json.loads(json.dumps(source)))
    saved = json.loads(json.dumps(app._serialize()))
    assert saved["sync_revision"] == 11
    assert saved["bin"][0]["proxy"] == {"scale": 0.5}
    assert saved["clips"][0]["colour_grade"] == {"temperature": 12}
    assert saved["music"]["beat_grid"] == [0.0, 0.5]
    print("PASS project, bin, clip and music metadata survives reopen and save")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
