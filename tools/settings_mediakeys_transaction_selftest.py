#!/usr/bin/env python3
"""Disjoint Settings/media-key writes must never erase one another."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbmediakeys  # noqa: E402
import settings  # noqa: E402


class Pane:
    _save_settings = settings.Settings._save_settings


with tempfile.TemporaryDirectory() as td:
    os.environ["NB_HOME"] = td
    path = Path(td) / ".config/notebook/settings.json"
    path.parent.mkdir(parents=True)
    original = {"timezone": "UTC", "sound.volume": 80,
                "sound.muted": False, "future": {"keep": True}}
    path.write_text(json.dumps(original), encoding="utf-8")
    old_cfg = settings.CFG_FILE
    settings.CFG_FILE = os.fspath(path)
    try:
        pane = Pane()
        pane._settings_baseline = dict(original)
        pane._settings = dict(original, timezone="Asia/Tokyo")

        # Media key wins the race before the older Settings snapshot publishes.
        assert nbmediakeys._persist_sound(25, True)
        assert pane._save_settings()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["timezone"] == "Asia/Tokyo"
        assert saved["sound.volume"] == 25 and saved["sound.muted"] is True
        assert saved["future"] == {"keep": True}

        # The reverse order also preserves Settings' unrelated choice.
        pane._settings["timezone"] = "Europe/Paris"
        assert pane._save_settings()
        assert nbmediakeys._persist_sound(40, False)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["timezone"] == "Europe/Paris"
        assert saved["sound.volume"] == 40 and saved["sound.muted"] is False

        # A stale startup decision must not quarantine a healthy replacement
        # that another process published before Settings acquired the lock.
        path.write_text('"future settings"', encoding="utf-8")
        pane._settings_quarantine_pending = True
        pane._settings_baseline = {}
        pane._settings = {"timezone": "UTC"}
        path.write_text(json.dumps({"sound.volume": 22,
                                    "sound.muted": True}), encoding="utf-8")
        assert pane._save_settings()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved == {"sound.volume": 22, "sound.muted": True,
                         "timezone": "UTC"}
        assert not list(path.parent.glob("settings.json.damaged-*"))
    finally:
        settings.CFG_FILE = old_cfg

print("PASS Settings and media keys preserve disjoint concurrent changes")
print("RESULT: ALL PASS")
