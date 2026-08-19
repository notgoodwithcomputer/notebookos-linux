#!/usr/bin/env python3
"""Every visible Sleep route maps the lock surface before DPMS blanking."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import settings  # noqa: E402


def main() -> None:
    app = settings.Settings.__new__(settings.Settings)
    calls, direct = [], []
    old_popen, old_run = settings.subprocess.Popen, settings.run
    try:
        settings.subprocess.Popen = lambda argv: calls.append(argv)
        settings.run = lambda argv: (direct.append(argv) or (0, ""))
        app._on_power(None, "sleep")
    finally:
        settings.subprocess.Popen, settings.run = old_popen, old_run
    assert calls == [["python3", "/opt/notebook/de/login.py", "--lock", "--sleep"]]
    assert not direct
    print("PASS Settings Sleep uses the lock-before-blank path")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
