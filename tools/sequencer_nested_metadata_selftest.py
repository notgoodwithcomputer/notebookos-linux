#!/usr/bin/env python3
"""Regression: newer track/clip fields survive Sequencer normalization."""
import copy
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import sequencer as Q  # noqa: E402


def main():
    app = Q.Sequencer.__new__(Q.Sequencer)
    app.pos = 0.0
    app.rec_start = None
    project = {
        "version": 4,
        "length": 60.0,
        "tracks": [{
            "name": "Voice",
            "clips": [{
                "s": 1.0, "e": 3.0,
                "transcript": {"text": "count in"},
            }],
            "channel_strip": {"colour": "amber", "bus": 2},
        }],
    }
    app._apply(copy.deepcopy(project))
    saved = json.loads(json.dumps(app._serialize()))
    track = saved["tracks"][0]
    clip = track["clips"][0]
    assert track["channel_strip"] == {"colour": "amber", "bus": 2}
    assert clip["transcript"] == {"text": "count in"}
    assert Q.clip_copy(app.tracks[0]["clips"][0])["transcript"] == {
        "text": "count in"}
    print("PASS newer track and clip metadata survives reopen, save and copy")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
