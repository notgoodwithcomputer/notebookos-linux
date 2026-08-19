#!/usr/bin/env python3
"""Stale-temp cleanup must not unlink a live locked export draft."""
import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path
DE = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
      "rootfs-overlay/opt/notebook/de")
sys.path.insert(0, str(DE))
import nbapp

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, ".nbw-live.tmp")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"active export")
    os.utime(path, (time.time() - 7200,) * 2)
    fcntl.flock(fd, fcntl.LOCK_EX)
    nbapp._REAPED_TMP.discard(td); nbapp._reap_stale_tmp(td)
    assert os.path.exists(path), "live locked export was reaped"
    fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
    nbapp._REAPED_TMP.discard(td); nbapp._reap_stale_tmp(td)
    assert not os.path.exists(path), "unlocked abandoned draft was not reaped"
print("PASS stale cleanup preserves active export drafts and removes abandoned ones")
print("RESULT: PASS")
