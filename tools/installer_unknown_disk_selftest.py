#!/usr/bin/env python3
"""Unrecognised disk bytes must never receive a calm 'empty' warning."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import installer  # noqa: E402


def main() -> None:
    obj = installer.Installer.__new__(installer.Installer)
    obj.tools = {"lsblk": "/sbin/lsblk"}
    old_run = installer.run_cmd
    try:
        installer.run_cmd = lambda _cmd: (0,
            'NAME="sdb" SIZE="64000000000" FSTYPE="" LABEL="" '
            'TYPE="disk" PARTTYPE=""\n')
        contents = obj._disk_contents("sdb")
    finally:
        installer.run_cmd = old_run
    assert contents == "UNKNOWN"
    assert "empty" not in obj._contents_line(contents).lower()
    print("PASS raw or unrecognised disks retain the full destructive warning")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
