#!/usr/bin/env python3
"""Display-free PID-reuse contract for app activity markers."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbapp  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-marker-") as root:
    old_dir = nbapp._APP_DIR
    old_flag = nbapp._APP_FLAG
    nbapp._APP_DIR = root
    nbapp._APP_FLAG = str(Path(root, "active"))
    try:
        pid = str(os.getpid())
        marker = Path(root, pid)
        token = nbapp._proc_start_token(pid)
        marker.write_text("calendar\n%s\n" % token)
        assert nbapp.app_marker_live(pid)
        print("PASS matching PID and birth token is live")

        marker.write_text("calendar\nnot-the-birth-token\n")
        assert not nbapp.app_marker_live(pid)
        print("PASS a reused live PID cannot revive a stale marker")

        marker.write_text("calendar\n")
        assert not nbapp.app_marker_live(pid)
        print("PASS unsafe legacy PID-only markers are not trusted")

        # A malformed legacy marker is never trusted merely because Linux has
        # reused its numeric PID for an unrelated process.
        nbapp._refresh_app_flag()
        assert not marker.exists()
        print("PASS refresh reaps a malformed marker after PID reuse")

        real_replace = nbapp.os.replace
        observed = []

        def interleaved_replace(src, dst):
            # A competing refresh occurs after the complete temp was written
            # but before publication.  It cannot see or reap a partial numeric
            # PID marker because the temp name is deliberately nonnumeric.
            observed.append(Path(src).read_text())
            assert not Path(dst).exists()
            nbapp._refresh_app_flag()
            real_replace(src, dst)

        nbapp.os.replace = interleaved_replace
        try:
            nbapp._register_app()
        finally:
            nbapp.os.replace = real_replace
        assert observed and nbapp.app_marker_live(pid)
        assert Path(nbapp._APP_FLAG).exists()
        print("PASS registration publishes a complete marker atomically")

        Path(root, "99999999").write_text("calendar\n1\n")
        assert not nbapp.app_marker_live("99999999")
        print("PASS dead process markers are not live")
    finally:
        nbapp._APP_DIR = old_dir
        nbapp._APP_FLAG = old_flag

print("RESULT: ALL PASS")
