#!/usr/bin/env python3
"""Display-free command contract for saved settings on mirrored outputs."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbprefs  # noqa: E402


XR = """eDP-1 connected primary 1920x1080+0+0
HDMI-1 connected 3840x2160+0+0
DP-1 disconnected
"""


def exercise(fn, value, expected_canvas):
    calls = []
    real = nbprefs.run

    def fake(cmd, timeout=4):
        calls.append(cmd)
        return (0, XR) if cmd == ["xrandr"] else (0, "")

    nbprefs.run = fake
    try:
        assert fn(value) is True
    finally:
        nbprefs.run = real
    mirrors = [c for c in calls if "--scale-from" in c]
    assert mirrors == [["xrandr", "--output", "HDMI-1", "--auto",
                        "--scale-from", expected_canvas,
                        "--same-as", "eDP-1"]], calls
    assert not any("DP-1" in c for c in calls), calls


exercise(nbprefs.apply_resolution, "1280x720", "1280x720")
print("PASS saved resolution remirrors every connected external output")
exercise(nbprefs.apply_scale, "1.25", "2400x1350")
print("PASS saved scale remirrors to the primary logical canvas")
exercise(nbprefs.apply_scale, "1.0", "1920x1080")
print("PASS Normal scale resets a previously scaled display to native size")

calls = []
real = nbprefs.run
snapshots = [XR, "eDP-1 connected primary 1280x720+0+0\n"]


def hot_unplug(cmd, timeout=4):
    calls.append(cmd)
    if cmd == ["xrandr"]:
        return 0, snapshots.pop(0)
    if "HDMI-1" in cmd and "--scale-from" in cmd:
        return 1, "output disappeared"
    return 0, ""


nbprefs.run = hot_unplug
try:
    assert nbprefs.apply_resolution("1280x720") is True
finally:
    nbprefs.run = real
assert not any("--off" in call for call in calls), calls
print("PASS hot-unplug cannot turn a successful primary change into a false failure")

calls = []


def transient_failure(cmd, timeout=4):
    calls.append(cmd)
    if cmd == ["xrandr"]:
        return 0, XR
    if "HDMI-1" in cmd and "--scale-from" in cmd:
        return 1, "busy"
    return 0, ""


nbprefs.run = transient_failure
try:
    assert nbprefs.apply_resolution("1280x720") is True
finally:
    nbprefs.run = real
mirror_calls = [call for call in calls if "HDMI-1" in call
                and "--scale-from" in call]
assert len(mirror_calls) == 2 and not any("--off" in call for call in calls), calls
print("PASS a transient external failure retries without blacking out the TV")
print("RESULT: ALL PASS")
