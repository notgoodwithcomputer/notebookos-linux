#!/usr/bin/env python3
"""
opt/notebook/display.sh must drive EVERY connected screen.

The bug this guards: the session took the FIRST connected output and
configured only that, so a television on HDMI was found, listed, and never
switched on. There is no HDMI on the build host, so the script is driven
against recorded xrandr output with a stub xrandr that records its arguments.
"""
import os
import subprocess
import sys
import tempfile

SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/display.sh")

LAPTOP_PLUS_TV = """Screen 0: minimum 320 x 200, current 1920 x 1080
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.05*+  48.00
   1600x900      59.99
HDMI-1 connected 3840x2160+1920+0 (normal left inverted right x axis y axis) 1600mm x 900mm
   3840x2160     30.00 +  25.00
   1920x1080     60.00*   50.00
DP-2 disconnected (normal left inverted right x axis y axis)
"""
LAPTOP_ONLY = """Screen 0: minimum 320 x 200, current 1920 x 1080
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.05*+
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""
SIMPLEDRM = """Screen 0: minimum 1366 x 768, current 1366 x 768, maximum 1366 x 768
None-1 connected primary 1366x768+0+0 0mm x 0mm
   1366x768      60.00*
"""

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def run(xrandr_out):
    d = tempfile.mkdtemp(prefix="disp-")
    log = os.path.join(d, "calls")
    stub = os.path.join(d, "xrandr")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\n'
                 'if [ "$#" -eq 0 ]; then cat %s; exit 0; fi\n'
                 'echo "$@" >> %s\n' % (
                     os.path.join(d, "out.txt"), log))
    os.chmod(stub, 0o755)
    with open(os.path.join(d, "out.txt"), "w") as fh:
        fh.write(xrandr_out)
    open(log, "w").close()
    env = dict(os.environ, PATH=d + ":/usr/bin:/bin")
    subprocess.run(["/bin/sh", SH], env=env, capture_output=True, timeout=60)
    return [ln for ln in open(log).read().splitlines() if ln.strip()]


print("-- a laptop with a television on HDMI")
calls = run(LAPTOP_PLUS_TV)
for c in calls:
    print("     xrandr %s" % c)
check("the internal panel is configured and made primary",
      any("eDP-1" in c and "--primary" in c for c in calls), calls)
check("the TELEVISION is switched on too",
      any("HDMI-1" in c for c in calls), calls)
check("...and it MIRRORS the panel rather than extending the desktop",
      any("HDMI-1" in c and "--same-as" in c for c in calls), calls)
check("the TV uses the panel's shared logical mode",
      any("HDMI-1" in c and "--mode 1920x1080" in c for c in calls), calls)
check("mirroring never overlaps unequal automatic modes",
      not any("HDMI-1" in c and "--auto" in c and
              "--scale-from" not in c for c in calls), calls)
check("a disconnected output is not configured",
      not any("DP-2" in c and "--auto" in c for c in calls), calls)

print("-- the same laptop with nothing plugged in")
calls = run(LAPTOP_ONLY)
check("the panel is still configured", any("eDP-1" in c for c in calls), calls)
check("a disconnected HDMI is not mirrored",
      not any("HDMI-1" in c and "--same-as" in c for c in calls), calls)

print("-- a plain EFI framebuffer (no KMS driver, one fixed output)")
calls = run(SIMPLEDRM)
check("the single output is configured",
      any("None-1" in c for c in calls), calls)
check("nothing is mirrored onto a screen that does not exist",
      not any("--same-as" in c for c in calls), calls)

# ---------------------------------------------------------------------------
print("-- Settings > Displays drives THE SAME screen this script does")
# The controls on that page act on one output. It has to be the one the person
# is sitting in front of, which is the one this script gives the origin to.
# Settings picked the FIRST line carrying " connected" instead, and xrandr
# lists outputs in the server's order: on a machine that enumerates HDMI-1
# ahead of eDP-1, "Resolution" offered the television's 3840x2160 and re-moded
# the television, and "Size of everything" supersampled the television — so
# somebody sitting at a laptop changed a setting and watched nothing happen to
# the screen in front of them.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="disp-home-"))
import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
import settings                                                  # noqa: E402


def sh_primary(xr):
    """Which output opt/notebook/display.sh makes primary, by its own rule."""
    for line in xr.splitlines():
        name = line.split()[0] if line.split() else ""
        if " connected" in line and name.startswith(("eDP", "LVDS", "DSI")):
            return name
    for line in xr.splitlines():
        if " connected" in line:
            return line.split()[0]
    return ""


# HDMI enumerated FIRST — the layout that exposed this, and the one a laptop
# with the television listed ahead of its own panel really reports.
TV_FIRST = """Screen 0: minimum 320 x 200, current 1920 x 1080
HDMI-1 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm
   3840x2160     30.00 +  25.00
   1920x1080     60.00*   50.00
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.05*+  48.00
   1600x900      59.99
DP-2 disconnected (normal left inverted right x axis y axis)
"""
for label, xr in (("television listed first", TV_FIRST),
                  ("panel listed first", LAPTOP_PLUS_TV),
                  ("nothing plugged in", LAPTOP_ONLY),
                  ("a plain EFI framebuffer", SIMPLEDRM)):
    want = sh_primary(xr)
    got = settings.Settings._x_output(None, xr)
    check("%s: Settings drives %s, display.sh drives %s"
          % (label, got or "nothing", want or "nothing"), got == want)
# ...and it must therefore offer THAT screen's modes, not the television's.
modes = settings.Settings._x_modes(None, sh_primary(TV_FIRST), TV_FIRST)
check("the resolutions offered are the panel's own, not the television's",
      "1600x900" in modes and "3840x2160" not in modes, modes)

print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
