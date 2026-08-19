#!/usr/bin/env python3
"""Successful volume hotkeys must survive the next login."""

from pathlib import Path
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbmediakeys as mk  # noqa: E402


class OSD:
    def show_level(self, *_args):
        pass

    def show_note(self, *_args):
        pass


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["NB_HOME"] = td
        path = Path(td) / ".config/notebook/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"unrelated": {"keep": True},
                                    "sound.volume": 80,
                                    "sound.muted": False}), encoding="utf-8")
        obj = mk.MediaKeys.__new__(mk.MediaKeys)
        obj.osd = OSD()
        old_volume = mk._volume
        old_has_volume = mk.nbaudio.has_volume
        try:
            mk.nbaudio.has_volume = lambda: True
            mk._volume = lambda delta=None, toggle=False: (20, True)
            obj._on_key(mk.XF86_AudioLowerVolume)
        finally:
            mk._volume = old_volume
            mk.nbaudio.has_volume = old_has_volume
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["sound.volume"] == 20 and saved["sound.muted"] is True
        assert saved["unrelated"] == {"keep": True}

        before = path.read_bytes()
        mk._volume = lambda delta=None, toggle=False: (None, False)
        try:
            obj._on_key(mk.XF86_AudioLowerVolume)
        finally:
            mk._volume = old_volume
        assert path.read_bytes() == before

    print("PASS successful media-key sound changes persist without collateral loss")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
