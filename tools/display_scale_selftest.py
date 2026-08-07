#!/usr/bin/env python3
"""
display_scale_selftest — prove opt/notebook/display.sh picks the right interface
scale for real panels, including the panels where the right answer is "don't".

WHY A TEST AND NOT AN EYEBALL. The scale decision is three nested integer
comparisons in busybox ash, it runs once at boot on hardware the developer does
not own, and every way it can be wrong is SILENT: a panel that should double and
does not just looks small, and a panel that doubles when it should not loses the
bottom of every window. Neither says anything. The 1024x740 guard in particular
is dead code until a 2560x1440 panel exists to trip it, and dead code that has
never once been executed is not a guard.

METHOD. display.sh talks to the world through exactly one command, so the test
puts a FAKE xrandr first on PATH that prints a canned description of a given
panel, runs the real script, and reads back the scale it published. No display,
no hardware, no X server.

    python3 tools/display_scale_selftest.py
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPLAY_SH = os.path.join(
    os.path.dirname(_HERE), "buildroot", "board", "notebookos",
    "rootfs-overlay", "opt", "notebook", "display.sh")

# A connected-output line as xrandr really prints it, plus one mode line so the
# shape matches. `mm` of None means the panel reported no physical size, which
# is the common EDID failure and has its own branch in the script.
def xrandr_output(name, w, h, mm_w=None, mm_h=None, primary=True):
    phys = "%dmm x %dmm" % (mm_w, mm_h) if mm_w else "0mm x 0mm"
    head = "Screen 0: minimum 320 x 200, current %d x %d, maximum 16384 x 16384" % (w, h)
    line = "%s connected %s%dx%d+0+0 (normal left inverted right x axis y axis) %s" % (
        name, "primary " if primary else "", w, h, phys)
    modes = "   %dx%d     60.00*+" % (w, h)
    # Dense panels really do offer 1920x1080 as a scaled mode. This line is the
    # whole reason the old code was wrong, so every case carries it.
    if (w, h) != (1920, 1080):
        modes += "\n   1920x1080     60.00"
    return "\n".join([head, line, modes])


# (label, xrandr text, expected scale or None for "wrote nothing")
CASES = [
    ("MacBook-class 13.6\" 2560x1664",
     xrandr_output("eDP-1", 2560, 1664, 301, 195), "2"),
    ("15.6\" 4K 3840x2160",
     xrandr_output("eDP-1", 3840, 2160, 344, 193), "2"),
    ("13.3\" 1920x1080 (dense, but not doubled)",
     xrandr_output("eDP-1", 1920, 1080, 294, 165), "1"),
    # THE GUARD CASE. 221 DPI clears the density test, but halving 1440 gives
    # 720 -- twenty pixels under the 740 budget -- so it must stay at 1x.
    ("13\" 2560x1440 (density says 2x, budget says no)",
     xrandr_output("eDP-1", 2560, 1440, 294, 165), "1"),
    ("1366x768 netbook panel",
     xrandr_output("eDP-1", 1366, 768, 277, 156), "1"),
    ("4K panel with no EDID physical size",
     xrandr_output("eDP-1", 3840, 2160, None, None), "2"),
    ("1920x1080 panel with no EDID physical size",
     xrandr_output("eDP-1", 1920, 1080, None, None), "1"),
    ("55\" 1080p television on HDMI",
     xrandr_output("HDMI-1", 1920, 1080, 1218, 685), "1"),
    ("27\" 2560x1440 external monitor",
     xrandr_output("DP-1", 2560, 1440, 597, 336), "1"),
    # simpledrm with nothing usable: no geometry at all. The script must publish
    # NOTHING rather than guess, so the session falls back to 1.
    ("no parseable geometry",
     "Screen 0: minimum 320 x 200, current 0 x 0, maximum 0 x 0", None),
]


def run_case(text, tmpdir):
    """Run display.sh against a fake xrandr printing `text`. Return scale/None."""
    bindir = os.path.join(tmpdir, "bin")
    os.makedirs(bindir, exist_ok=True)
    fake = os.path.join(bindir, "xrandr")
    # The fake ignores its arguments: display.sh both QUERIES (bare `xrandr`)
    # and SETS (`xrandr --output ...`). Printing the same description for both
    # is right, because the panel's native mode is what --auto would have
    # selected anyway, so the geometry after setting equals the geometry before.
    with open(fake, "w") as fh:
        fh.write("#!/bin/sh\ncat <<'NBEOF'\n%s\nNBEOF\n" % text)
    os.chmod(fake, 0o755)

    scale_file = os.path.join(tmpdir, "nb-scale")
    if os.path.exists(scale_file):
        os.unlink(scale_file)

    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["NB_SCALE_FILE"] = scale_file
    proc = subprocess.run(["sh", DISPLAY_SH], env=env,
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return "<exit %d: %s>" % (proc.returncode, proc.stderr.strip()[:120])
    if not os.path.exists(scale_file):
        return None
    with open(scale_file) as fh:
        return fh.read().strip()


def main():
    if not os.path.exists(DISPLAY_SH):
        print("display.sh not found at %s" % DISPLAY_SH)
        return 1
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for label, text, expected in CASES:
            got = run_case(text, tmpdir)
            ok = (got == expected)
            if not ok:
                failures += 1
            print("%s  %-46s expected %-4s got %-4s" % (
                "ok  " if ok else "FAIL", label,
                expected if expected is not None else "-",
                got if got is not None else "-"))
    print("\n%d/%d passed" % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
