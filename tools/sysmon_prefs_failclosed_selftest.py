#!/usr/bin/env python3
"""Display-free fail-closed check for System Monitor preferences."""
import os
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import sysmon  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-sysmon-q-") as root:
    path = os.path.join(root, "sysmon.json")
    Path(path).write_text('"future preferences"', encoding="utf-8")
    app = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
    app._prefs_path = lambda: path
    app._sort_col = 4
    app._sort_order = sysmon.Gtk.SortType.DESCENDING
    app._prefs_quarantine_pending = False
    app._save_failure_told = False
    real_q = sysmon.nbapp.quarantine_unrecognized
    real_write = sysmon.nbapp.atomic_write_json
    writes = []
    sysmon.nbapp.quarantine_unrecognized = lambda _path: None
    sysmon.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        app._load_sort_prefs()
        assert app._prefs_quarantine_pending is True
        assert app._save_sort_prefs() is False
    finally:
        sysmon.nbapp.quarantine_unrecognized = real_q
        sysmon.nbapp.atomic_write_json = real_write
    wrote_store = any(args and args[0] == path for args in writes)
    assert Path(path).read_text() == '"future preferences"' and not wrote_store
    print("PASS failed preference preservation blocks replacement")

with tempfile.TemporaryDirectory(prefix="nb-sysmon-future-") as root:
    path = os.path.join(root, "sysmon.json")
    future = {"sort_col": 1, "sort_desc": False,
              "columns": {"memory": False}, "sample_seconds": 5}
    Path(path).write_text(json.dumps(future), encoding="utf-8")
    app = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
    app._prefs_path = lambda: path
    app._sort_col = 4
    app._sort_order = sysmon.Gtk.SortType.DESCENDING
    app._prefs_quarantine_pending = False
    app._prefs_extra = {}
    app._load_sort_prefs()
    # Simulate a header click changing the known preference before save.
    app._sort_col = 5
    app._sort_order = sysmon.Gtk.SortType.DESCENDING
    assert app._save_sort_prefs() is True
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    assert saved["sort_col"] == 5 and saved["sort_desc"] is True
    assert saved["columns"] == future["columns"]
    assert saved["sample_seconds"] == 5
    print("PASS unknown future monitor preferences survive a sort change")

print("RESULT: ALL PASS")
