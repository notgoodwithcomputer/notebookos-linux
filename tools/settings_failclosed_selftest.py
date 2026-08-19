#!/usr/bin/env python3
"""Display-free failed-quarantine contract for Settings."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import settings  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-settings-q-") as root:
    path = os.path.join(root, "settings.json")
    Path(path).write_text('"future settings"', encoding="utf-8")
    old = settings.CFG_FILE
    settings.CFG_FILE = path
    app = settings.Settings.__new__(settings.Settings)
    app._settings_quarantine_pending = False
    real_q = settings.nbapp.quarantine_unrecognized
    real_write = settings.nbapp.atomic_write_json
    writes = []
    settings.nbapp.quarantine_unrecognized = lambda _path: None
    settings.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        app._settings = app._load_settings()
        assert app._save_settings() is False
    finally:
        settings.nbapp.quarantine_unrecognized = real_q
        settings.nbapp.atomic_write_json = real_write
        settings.CFG_FILE = old
    wrote_store = any(args and args[0] == path for args in writes)
    assert Path(path).read_text() == '"future settings"' and not wrote_store
    assert app._settings_quarantine_pending and app._save_error
    print("PASS Settings blocks replacement after failed preservation")

print("RESULT: ALL PASS")
