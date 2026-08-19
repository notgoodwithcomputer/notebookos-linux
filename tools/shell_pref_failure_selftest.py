#!/usr/bin/env python3
"""Display-free persistence truthfulness for panel preferences."""
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="shell-pref-"))
import shell  # noqa: E402


with tempfile.TemporaryDirectory(prefix="shell-pref-store-") as tmp:
    old_dir, old_file = shell.CFG_DIR, shell.SHELL_FILE
    shell.CFG_DIR, shell.SHELL_FILE = tmp, os.path.join(tmp, "shell.json")
    Path(shell.SHELL_FILE).write_text(json.dumps({"unrelated": 7}),
                                     encoding="utf-8")
    notices = []
    real_write, real_post = shell.nbapp.atomic_write_json, shell.nbnotify.post
    app = shell.Panel.__new__(shell.Panel)
    app._clock_24h = True
    app._clock_seconds = False
    app._show_date = True
    app._label_idx = None
    app._pin_widths = lambda: None
    app._tick = lambda: None
    try:
        shell.nbnotify.post = lambda *args, **kwargs: notices.append((args, kwargs))
        shell.nbapp.atomic_write_json = lambda *_a, **_k: (_ for _ in ()).throw(
            OSError("disk full"))
        app._toggle_view("seconds")
        app._set_label(2)
        assert app._clock_seconds is False and app._label_idx is None
        assert json.loads(Path(shell.SHELL_FILE).read_text())["unrelated"] == 7
        assert len(notices) == 2

        shell.nbapp.atomic_write_json = real_write
        app._toggle_view("seconds")
        app._set_label(2)
        saved = json.loads(Path(shell.SHELL_FILE).read_text())
        assert app._clock_seconds is True and app._label_idx == 2
        assert saved["clock_seconds"] is True and saved["finder_label_idx"] == 2
        assert saved["unrelated"] == 7
    finally:
        shell.nbapp.atomic_write_json, shell.nbnotify.post = real_write, real_post
        shell.CFG_DIR, shell.SHELL_FILE = old_dir, old_file

print("PASS failed shell preference writes do not alter visible state")
print("PASS successful writes preserve unrelated preferences")
print("RESULT: PASS")
