#!/usr/bin/env python3
"""Regression: System Monitor owns and cancels its /proc poll."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import sysmon  # noqa: E402


def main():
    app = sysmon.SystemMonitor.__new__(sysmon.SystemMonitor)
    app._alive = True
    app._refresh_source = 73
    removed = []
    old_remove = sysmon.GLib.source_remove
    try:
        sysmon.GLib.source_remove = removed.append
        assert app._on_destroy() is False
        assert app._alive is False and app._refresh_source == 0
        assert removed == [73]
        assert app._on_destroy() is False
        assert removed == [73]
    finally:
        sysmon.GLib.source_remove = old_remove
    print("PASS /proc poll is cancelled exactly once at destroy")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
