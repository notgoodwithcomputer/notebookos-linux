#!/usr/bin/env python3
"""A stalled locale writer must not freeze Settings/login indefinitely."""

from pathlib import Path
import fcntl
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbi18n  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "locale.json")
        holder = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        acquired = nbi18n._lock_locale(path)
        elapsed = time.monotonic() - start
        old_config = nbi18n._config_path
        nbi18n._config_path = lambda: path
        try:
            assert nbi18n._update_locale(lang="fr") is False
        finally:
            nbi18n._config_path = old_config
        assert not os.path.exists(path)
        os.close(holder)
        assert acquired is None and elapsed < 0.75, elapsed
        acquired = nbi18n._lock_locale(path)
        assert acquired is not None
        os.close(acquired)
    print("PASS locale lock contention has a bounded UI-thread wait")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
