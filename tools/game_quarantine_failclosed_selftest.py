#!/usr/bin/env python3
"""Display-free fail-closed checks for 2048 and GBA metadata."""
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import g2048  # noqa: E402
import gbaemu  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-game-q-") as root:
    # 2048: valid JSON, foreign shape.
    path = os.path.join(root, "2048.json")
    Path(path).write_text('"foreign game"', encoding="utf-8")
    old = g2048.STATE_FILE
    g2048.STATE_FILE = path
    app = g2048.Game2048.__new__(g2048.Game2048)
    app._quarantine_pending = False
    real_q = g2048.nbapp.quarantine_unrecognized
    real_write = g2048.nbapp.atomic_write_json
    writes = []
    g2048.nbapp.quarantine_unrecognized = lambda _path: None
    g2048.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        app._quarantine_unrecognized_store()
        app.best, app.status = 0, "play"
        app.board, app.score, app._extra = [[0] * 4 for _ in range(4)], 0, {}
        app._save_failure_told = False
        assert app._save_best() is False
    finally:
        g2048.nbapp.quarantine_unrecognized = real_q
        g2048.nbapp.atomic_write_json = real_write
        g2048.STATE_FILE = old
    wrote_store = any(args and args[0] == path for args in writes)
    assert Path(path).read_text() == '"foreign game"' and not wrote_store
    assert app._quarantine_pending is True
    print("PASS 2048 blocks a save after failed preservation")

    # GBA emulator: same contract for private metadata writer.
    cfg = os.path.join(root, "gbaemu.json")
    Path(cfg).write_text("[]", encoding="utf-8")
    old_path, old_dir = gbaemu.CFG_PATH, gbaemu.CFG_DIR
    gbaemu.CFG_PATH, gbaemu.CFG_DIR = cfg, root
    emu = gbaemu.GbaEmu.__new__(gbaemu.GbaEmu)
    real_q = gbaemu.nbapp.quarantine_unrecognized
    gbaemu.nbapp.quarantine_unrecognized = lambda _path: None
    try:
        emu._state_meta = emu._load_state_meta()
        assert emu._save_state_meta() is False
    finally:
        gbaemu.nbapp.quarantine_unrecognized = real_q
        gbaemu.CFG_PATH, gbaemu.CFG_DIR = old_path, old_dir
    assert json.loads(Path(cfg).read_text()) == []
    assert emu._meta_quarantine_pending is True
    print("PASS GBA metadata blocks a save after failed preservation")

print("RESULT: ALL PASS")
