#!/usr/bin/env python3
"""The Displays page reports the selected panel's active mode."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import settings  # noqa: E402


HDMI = """HDMI-1 connected 3840x2160+0+0
   3840x2160  60.00*
eDP-1 connected primary 1920x1080+0+0
   1920x1080  60.00*
   1366x768   60.00
"""


def main() -> None:
    app = settings.Settings.__new__(settings.Settings)
    assert app._x_current("eDP-1", HDMI) == "1920x1080"
    blocks = HDMI.split("eDP-1 connected", 1)
    reversed_order = "eDP-1 connected" + blocks[1] + blocks[0]
    assert app._x_current("eDP-1", reversed_order) == "1920x1080"
    assert app._x_current("HDMI-1", HDMI) == "3840x2160"
    print("PASS active resolution is scoped to the selected XRandR output")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
