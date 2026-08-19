#!/usr/bin/env python3
"""Playback control selection falls through Master to real ALSA controls."""
import sys
from pathlib import Path

DE = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
      "rootfs-overlay/opt/notebook/de")
sys.path.insert(0, str(DE))
import nbaudio

old = nbaudio._run
def fake(cmd, timeout=4):
    ctl = cmd[-1]
    if ctl == "Speaker":
        return 0, "Mono: Playback 55 [55%] [on]"
    return 1, ""
try:
    nbaudio._run = fake
    assert nbaudio.playback_control() == "Speaker"
    assert nbaudio.playback_control(require_switch=True) == "Speaker"
finally:
    nbaudio._run = old
print("PASS Speaker is used when a device has no Master control")
print("RESULT: PASS")
