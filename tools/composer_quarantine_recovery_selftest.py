#!/usr/bin/env python3
"""Successful Composer quarantine reopens persistence for the fresh song."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import composer  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="composer-quarantine-") as td:
        old = composer.STATE_FILE
        composer.STATE_FILE = os.path.join(td, "composer.json")
        try:
            Path(composer.STATE_FILE).write_text("not json", encoding="utf-8")
            app = composer.Composer.__new__(composer.Composer)
            app._read_only = False; app._session_warning = ""
            song = app._load_session()
            assert not app._read_only and not os.path.exists(composer.STATE_FILE)
            app.editor = type("Editor", (), {"song": song})()
            assert app._save_session() and os.path.isfile(composer.STATE_FILE)
            print("PASS successful quarantine permits the fresh song to persist")
            print("RESULT: PASS")
            return 0
        finally:
            composer.STATE_FILE = old


if __name__ == "__main__":
    raise SystemExit(main())
